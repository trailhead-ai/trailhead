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
heading line up to the next terminator heading, or the end of the document —
whichever comes first. A terminator heading is an ATX `##` or `#` heading,
OR a setext heading (a non-blank title line immediately followed, with no
blank line between, by a line of only `=` for an H1 or only `-` for an H2 —
the CommonMark disambiguation that keeps a bare `---` divider preceded by a
blank line from being mistaken for a heading boundary). A heading-looking
line found inside a fenced code block (``` or ~~~, any info string) is never
treated as that terminator: agent-instruction files are full of fenced
examples that themselves illustrate this very convention, and a fence-blind
scan would let an illustrative `##` inside someone's example truncate — or,
worse, extend past an unmasked H1 into — the real section.

A document may contain more than one unfenced `## Project Maturity` heading
(a rebase artifact, a copy-pasted reference block from another repository's
agent-instruction file). This is itself an ambiguous declaration, routed the
same as multiple distinct vocabulary words within one section — never
resolved by picking the first heading's value, or the "highest" of the
values found, both of which risk understating what the repository actually
declared.

The closed vocabulary is exactly three words — `prototype`, `early`,
`production` — matched case-insensitively, on a word boundary, anywhere
within the section body, so a declaration may carry surrounding prose
("This repository is at the Production level, per the last review."). A
level word appearing anywhere OUTSIDE the section — in ordinary prose
elsewhere in the file — is not a declaration and never matches: only a word
found inside the section body counts, which is what lets an absent section
and a declared one resolve differently even when the same word appears
somewhere in either document. CAUTION for anyone authoring a declaration:
this same tolerance for surrounding prose means a rationale sentence that
happens to name a SECOND vocabulary word (e.g. explaining an `early`
declaration by saying full production ceremony is premature) makes the
whole section ambiguous — keep any comparison to another level out of the
section body.

A `## Project Maturity` section whose body contains none of the three words
is an invalid declaration, not an absence. A section whose body contains
MORE THAN ONE distinct vocabulary word is an ambiguous declaration, not a
declaration of whichever word happens to appear first: first-match-wins over
free-form prose would let a body like "No longer a prototype; this is
production." resolve to `prototype` — the fail-UNSAFE direction (declaring a
lower level than what was actually stated) this feature exists to prevent.
All outcomes other than a single unambiguous declared word — absent,
invalid, ambiguous — default to the same `production` level, but are
reported through three distinct reason tokens (`section-absent` /
`invalid-value` / `ambiguous-value`) so a caller never has to guess which
happened from the resolved level alone. The offending value reported
alongside `invalid-value` (the section body's own text) or `ambiguous-value`
(the conflicting words found, in order of first appearance, or — for
multiple headings with no vocabulary word at all — the combined body text)
is, in both cases, sanitized before being reported: since it is repo content
an arbitrary contributor can author and it is about to be echoed into
session prose, it is collapsed to a single line, stripped of the full Cc
control-character category (C0, DEL, and the C1 block) and of Unicode
format/bidi-control characters and variation selectors (zero-width joiners,
bidi overrides and isolates, invisible "tag" characters, variation
selectors, and the like — the categories abused to smuggle instructions into
text that renders as innocuous), and bounded to a fixed maximum length.

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
       UTF-8. This never resolves to any level.
       (`reason-code: empty-stdin` / `reason-code: invalid-utf8-stdin`,
       printed to stderr.)

       Zero-byte stdin fails closed unconditionally, and DOES reach this
       script in practice: a genuinely empty (0-byte) agent-instruction file
       piped straight in (`cat CLAUDE.md | maturity_resolve.py`) produces
       exactly this input, and this script cannot tell that apart from a
       failed read that produced no bytes for some other reason — so both
       are treated identically, fail-closed, rather than risking either
       being read as a deliberate (if empty) declaration. A repository with
       no agent-instruction file at all is a separate case, handled by the
       caller before invocation (it never invokes this script), not by this
       exit path.

       A file that EXISTS and has at least one byte of content — even if
       that content is only whitespace, e.g. a single trailing newline — is
       NOT this fail-closed case: it decodes fine and simply declares
       nothing, resolving via the ordinary section-absent path to
       `production`, exit 0, same as any other file with no `##
       Project Maturity` section. The distinction that matters is the raw
       byte count being exactly zero, not whether the decoded text is
       blank — a whitespace-only file has bytes; only a truly empty read
       does not.
"""

from __future__ import annotations

import re
import sys

_LEVELS = ("prototype", "early", "production")
_DEFAULT_LEVEL = "production"

_HEADING_RE = re.compile(r"^##\s+Project Maturity\s*$", re.IGNORECASE)
_TERMINATOR_HEADING_RE = re.compile(r"^#{1,2}\s")
_SETEXT_H1_UNDERLINE_RE = re.compile(r"^=+\s*$")
_SETEXT_H2_UNDERLINE_RE = re.compile(r"^-+\s*$")
_FENCE_START_RE = re.compile(r"^(`{3,}|~{3,})")
_LEVEL_WORD_RE = re.compile(r"\b(?:" + "|".join(_LEVELS) + r")\b", re.IGNORECASE)
# Full Cc (control) category: C0 (U+0000-U+001F), DEL (U+007F), and the C1
# block (U+0080-U+009F) — all three are category Cc, not just the ASCII
# subset. U+009B (CSI) is the 8-bit equivalent of ESC [, so leaving the C1
# block unstripped is an escape-sequence injection risk for any terminal
# that honours 8-bit C1.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# Unicode format-control (category Cf) code points relevant to text-smuggled
# instructions: zero-width joiners/spaces, bidi marks/embeddings/overrides/
# isolates, the BOM, interlinear annotation marks, and the invisible Unicode
# "tag" block used for steganographic prompt injection. Hand-enumerated by
# codepoint rather than looked up via `unicodedata`, which this script's
# stdlib-only import allowlist ({__future__, re, sys}) does not admit.
#
# Also strips the Unicode variation selectors (U+FE00-FE0F and the
# supplement U+E0100-E01EF) alongside the Cf strip above: these are category
# Mn, not Cf, so the format-control pass alone would miss them, but ~190 of
# them fit inside the offending-value length bound and are exactly the
# codepoints behind current invisible-Unicode steganography (one hidden
# selector attached to an innocuous visible character).
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
    "\ufe00-\ufe0f"
    "\U000e0100-\U000e01ef"
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


def _is_terminator_start(lines: list[str], fenced: list[bool], i: int) -> bool:
    """True when line `i` (already known unfenced) begins a heading that
    terminates a section: an ATX `#`/`##` heading on that line itself, or a
    setext heading — a non-blank title line immediately followed (no blank
    line between) by an unfenced line of only `=` (setext H1) or only `-`
    (setext H2).

    Setext H2 is included deliberately: a `---`/`===` underline immediately
    below prose is, per CommonMark, indistinguishable from a real heading
    boundary, and the ATX terminator already treats `##` the same as `#`.
    Requiring the title line to be non-blank is what keeps an ordinary
    thematic break (a bare `---` preceded by a blank line, a common plain
    divider) from being mistaken for a setext H2 — CommonMark applies the
    same disambiguation."""
    if _TERMINATOR_HEADING_RE.match(lines[i]):
        return True
    if lines[i].strip() == "":
        return False
    if i + 1 >= len(lines) or fenced[i + 1]:
        return False
    nxt = lines[i + 1]
    return bool(_SETEXT_H1_UNDERLINE_RE.match(nxt) or _SETEXT_H2_UNDERLINE_RE.match(nxt))


def _extract_sections(text: str) -> list[str]:
    """Return the body of every unfenced `## Project Maturity` heading in
    the document, in order — not just the first. A document with more than
    one such heading is caught by the caller as ambiguous rather than
    letting the first heading win silently. Each body spans every line
    after its heading line up to (but not including) the next unfenced
    terminator heading (ATX `##`/`#`, or setext H1/H2), or the end of the
    document."""
    lines = re.split(r"\r\n|\r|\n", text)
    fenced = _fence_mask(lines)

    sections: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if not fenced[i] and _HEADING_RE.match(lines[i]):
            j = i + 1
            body_lines: list[str] = []
            while j < n:
                if not fenced[j] and _is_terminator_start(lines, fenced, j):
                    break
                body_lines.append(lines[j])
                j += 1
            sections.append("\n".join(body_lines))
            i = j
        else:
            i += 1
    return sections


def _sanitize(raw: str) -> str:
    collapsed = re.sub(r"\s+", " ", raw).strip()
    stripped = _CONTROL_CHAR_RE.sub("", collapsed)
    stripped = _FORMAT_CONTROL_CHAR_RE.sub("", stripped)
    if len(stripped) > _MAX_OFFENDING_VALUE_LEN:
        keep = _MAX_OFFENDING_VALUE_LEN - len(_TRUNCATION_SUFFIX)
        stripped = stripped[:keep] + _TRUNCATION_SUFFIX
    return stripped


def _distinct_levels(section_text: str) -> list[str]:
    """Return the distinct vocabulary words found in `section_text`, in
    order of first appearance, case-normalized. Repeating the SAME word
    does not add a second entry — only genuinely differing words do."""
    distinct_levels: list[str] = []
    for raw_match in _LEVEL_WORD_RE.findall(section_text):
        level = raw_match.lower()
        if level not in distinct_levels:
            distinct_levels.append(level)
    return distinct_levels


def resolve(text: str) -> tuple[str, str, str | None]:
    """Return (level, reason, offending_value) for an agent-instruction
    file's body. `offending_value` is None unless `reason` is
    `invalid-value` or `ambiguous-value`."""
    sections = _extract_sections(text)
    if not sections:
        return _DEFAULT_LEVEL, "section-absent", None

    if len(sections) > 1:
        # More than one unfenced `## Project Maturity` heading is itself an
        # ambiguous declaration — the same routing as multiple distinct
        # vocabulary words within one section, and for the same reason:
        # picking whichever heading appears first (or "highest") would be
        # the fail-UNSAFE direction this whole feature exists to prevent.
        combined = "\n".join(sections)
        distinct = _distinct_levels(combined)
        offending = ", ".join(distinct) if distinct else combined
        return _DEFAULT_LEVEL, "ambiguous-value", _sanitize(offending)

    section = sections[0]
    distinct_levels = _distinct_levels(section)

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
