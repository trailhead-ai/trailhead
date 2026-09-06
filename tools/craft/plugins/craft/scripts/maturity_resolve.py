#!/usr/bin/env python3
"""Maturity resolver — reads a repository's agent-instruction file on stdin
and resolves its declared project-maturity level, mirroring the emit-
tokens-on-stdout shape of the sibling `candidate_set.py` and `covers_gate.py`
gates.

Usage:
    cat CLAUDE.md | maturity_resolve.py

The agent-instruction file body arrives on stdin; there is no flag — this
gate derives a value from a document alone, the same convention
`candidate_set.py`'s docstring names for that kind of gate.

The declaration lives in a `## Project Maturity` section, beside the
`## Dependency Posture` block that already establishes the per-repository
declaration precedent in this repository's own agent-instruction file. The
heading is matched case-insensitively at line start (a mid-paragraph mention
does not satisfy it), and the section's body is everything after that
heading line up to the next `##` or `#` heading, or the end of the document —
whichever comes first. A heading-looking line found inside a fenced code
block (``` or ~~~, any info string) is never treated as that terminator:
agent-instruction files are full of fenced examples that themselves
illustrate this very convention, and a fence-blind scan would let an
illustrative `##` inside someone's example truncate — or, worse, extend past
an unmasked H1 into — the real section.

The closed vocabulary is exactly three words — `prototype`, `early`,
`production` — matched case-insensitively, on a word boundary, anywhere
within the section body, so a declaration may carry surrounding prose
("This repository is at the Production level, per the last review."). A
level word appearing anywhere OUTSIDE the section — in ordinary prose
elsewhere in the file — is not a declaration and never matches: only a word
found inside the section body counts, which is what lets an absent section
and a declared one resolve differently even when the same word appears
somewhere in either document.

A `## Project Maturity` section whose body contains none of the three words
is an invalid declaration, not an absence. A section whose body contains
MORE THAN ONE distinct vocabulary word is an ambiguous declaration, not a
declaration of whichever word happens to appear first: first-match-wins over
free-form prose would let a body like "No longer a prototype; this is
production." resolve to `prototype` — the fail-UNSAFE direction (declaring a
lower level than what was actually stated) this feature exists to prevent.
All three outcomes — absent, invalid, ambiguous — default to the same
`production` level, but are reported through three distinct reason tokens
(`section-absent` / `invalid-value` / `ambiguous-value`) so a caller never
has to guess which happened from the resolved level alone. The offending
value reported alongside `invalid-value` (the section body's own text) or
`ambiguous-value` (the conflicting words found, in order of first
appearance) is, in both cases, sanitized before being reported: since it is
repo content an arbitrary contributor can author and it is about to be
echoed into session prose, it is collapsed to a single line, stripped of
C0/DEL control characters and of Unicode format/bidi-control characters
(zero-width joiners, bidi overrides and isolates, invisible "tag"
characters, and the like — the categories abused to smuggle instructions
into text that renders as innocuous), and bounded to a fixed maximum length.

Stdout on success (exit 0), two or three lines:

    level: prototype|early|production
    reason: declared|section-absent|invalid-value|ambiguous-value
    offending-value: <sanitized text>

The third line is present only when `reason` is `invalid-value` or
`ambiguous-value`.

Exit codes:
    0  resolved — a level was determined, whether declared, defaulted from
       an absent section, or defaulted from an invalid or ambiguous declared
       value. NEVER exits 0 without printing a `level:` line.
    2  fail-closed — no bytes at all on stdin, or stdin that is not valid
       UTF-8. This never resolves to any level: a repository with no
       agent-instruction file at all is mapped to the absence path by the
       caller before invocation, and is never expected to reach this script
       as zero-byte stdin.
       (`reason-code: empty-stdin` / `reason-code: invalid-utf8-stdin`,
       printed to stderr.)

       A file that EXISTS but is empty or whitespace-only is NOT this
       fail-closed case — it has bytes (even if only whitespace), decodes
       fine, and simply declares nothing: it resolves via the ordinary
       section-absent path to `production`, exit 0, same as any other file
       with no `## Project Maturity` section. Only a read that produced zero
       bytes at all — the signature of a failed read, never a real empty
       file's content — fails closed; that distinction is why the check
       below is on the raw byte count, not on whether the decoded text is
       blank.
"""

from __future__ import annotations

import re
import sys

_LEVELS = ("prototype", "early", "production")
_DEFAULT_LEVEL = "production"

_HEADING_RE = re.compile(r"^##\s+Project Maturity\s*$", re.IGNORECASE)
_TERMINATOR_HEADING_RE = re.compile(r"^#{1,2}\s")
_FENCE_START_RE = re.compile(r"^(`{3,}|~{3,})")
_LEVEL_WORD_RE = re.compile(r"\b(?:" + "|".join(_LEVELS) + r")\b", re.IGNORECASE)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
# Unicode format-control (category Cf) code points relevant to text-smuggled
# instructions: zero-width joiners/spaces, bidi marks/embeddings/overrides/
# isolates, the BOM, interlinear annotation marks, and the invisible Unicode
# "tag" block used for steganographic prompt injection. Hand-enumerated by
# codepoint rather than looked up via `unicodedata`, which this script's
# stdlib-only import allowlist ({__future__, re, sys}) does not admit.
_FORMAT_CONTROL_CHAR_RE = re.compile(
    "["
    "­"
    "؀-؅"
    "؜"
    "۝"
    "܏"
    "࣢"
    "᠎"
    "​-‏"
    "‪-‮"
    "⁠-⁤"
    "⁦-⁯"
    "﻿"
    "￹-￻"
    "\U000110bd"
    "\U000110cd"
    "\U0001bca0-\U0001bca3"
    "\U0001d173-\U0001d17a"
    "\U000e0001"
    "\U000e0020-\U000e007f"
    "]"
)
_MAX_OFFENDING_VALUE_LEN = 200
_TRUNCATION_SUFFIX = " …(truncated)"

_EMPTY_STDIN_REASON_CODE = "empty-stdin"
_INVALID_UTF8_STDIN_REASON_CODE = "invalid-utf8-stdin"


def _err(msg: str) -> None:
    print(f"maturity-resolve: {msg}", file=sys.stderr)


def _fence_mask(lines: list[str]) -> list[bool]:
    """Return one boolean per line — True where the line is a fence marker
    or a line inside a fenced code block (``` or ~~~, any info string). A
    fence closes only on a same-character marker at least as long as the one
    that opened it, per CommonMark. Heading detection skips masked lines, so
    a heading-looking line inside someone's fenced example is never mistaken
    for real document structure."""
    masked = [False] * len(lines)
    fence_char: str | None = None
    fence_len = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if fence_char is None:
            m = _FENCE_START_RE.match(stripped)
            if m:
                fence_char = m.group(1)[0]
                fence_len = len(m.group(1))
                masked[i] = True
            continue
        masked[i] = True
        m = _FENCE_START_RE.match(stripped)
        if m and m.group(1)[0] == fence_char and len(m.group(1)) >= fence_len:
            fence_char = None
    return masked


def _extract_section(text: str) -> str | None:
    """Return the `## Project Maturity` section body, or None if no such
    (unfenced) heading is present. The body spans every line after the
    heading line up to (but not including) the next unfenced `##` or `#`
    heading, or the end of the document."""
    lines = re.split(r"\r\n|\r|\n", text)
    fenced = _fence_mask(lines)

    start = None
    for i, line in enumerate(lines):
        if fenced[i]:
            continue
        if _HEADING_RE.match(line):
            start = i + 1
            break
    if start is None:
        return None

    body_lines: list[str] = []
    for i in range(start, len(lines)):
        line = lines[i]
        if not fenced[i] and _TERMINATOR_HEADING_RE.match(line):
            break
        body_lines.append(line)
    return "\n".join(body_lines)


def _sanitize(raw: str) -> str:
    collapsed = re.sub(r"\s+", " ", raw).strip()
    stripped = _CONTROL_CHAR_RE.sub("", collapsed)
    stripped = _FORMAT_CONTROL_CHAR_RE.sub("", stripped)
    if len(stripped) > _MAX_OFFENDING_VALUE_LEN:
        keep = _MAX_OFFENDING_VALUE_LEN - len(_TRUNCATION_SUFFIX)
        stripped = stripped[:keep] + _TRUNCATION_SUFFIX
    return stripped


def resolve(text: str) -> tuple[str, str, str | None]:
    """Return (level, reason, offending_value) for an agent-instruction
    file's body. `offending_value` is None unless `reason` is
    `invalid-value` or `ambiguous-value`."""
    section = _extract_section(text)
    if section is None:
        return _DEFAULT_LEVEL, "section-absent", None

    distinct_levels: list[str] = []
    for raw_match in _LEVEL_WORD_RE.findall(section):
        level = raw_match.lower()
        if level not in distinct_levels:
            distinct_levels.append(level)

    if not distinct_levels:
        return _DEFAULT_LEVEL, "invalid-value", _sanitize(section)

    if len(distinct_levels) > 1:
        return _DEFAULT_LEVEL, "ambiguous-value", _sanitize(", ".join(distinct_levels))

    return distinct_levels[0], "declared", None


def main(argv: list[str]) -> int:
    del argv  # no flags — the whole interface is stdin

    raw = sys.stdin.buffer.read()
    if not raw:
        _err("stdin is empty")
        _err(f"reason-code: {_EMPTY_STDIN_REASON_CODE}")
        return 2

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        _err(f"stdin is not valid UTF-8: {e}")
        _err(f"reason-code: {_INVALID_UTF8_STDIN_REASON_CODE}")
        return 2

    level, reason, offending_value = resolve(text)

    print(f"level: {level}")
    print(f"reason: {reason}")
    if offending_value is not None:
        print(f"offending-value: {offending_value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
