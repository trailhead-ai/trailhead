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
heading line up to the next `##` heading or the end of the document.

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
is an invalid declaration, not an absence: the two are reported through
distinct reason tokens (`section-absent` vs. `invalid-value`) so a caller
never has to guess which happened from the resolved level alone, since both
default to the same `production` level. The offending value reported
alongside `invalid-value` is the section body's own text — collapsed to a
single line and stripped of control characters, since it is repo content an
arbitrary contributor can author and it is about to be echoed into session
prose.

Stdout on success (exit 0), two or three lines:

    level: prototype|early|production
    reason: declared|section-absent|invalid-value
    offending-value: <sanitized text>

The third line is present only when `reason: invalid-value`.

Exit codes:
    0  resolved — a level was determined, whether declared, defaulted from
       an absent section, or defaulted from an invalid declared value.
       NEVER exits 0 without printing a `level:` line.
    2  fail-closed — empty or non-UTF-8 stdin. This never resolves to any
       level: a repository with no agent-instruction file at all is mapped
       to the absence path by the caller before invocation, and is never
       expected to reach this script as empty stdin.
       (`reason-code: empty-stdin` / `reason-code: invalid-utf8-stdin`,
       printed to stderr.)
"""

from __future__ import annotations

import re
import sys

_LEVELS = ("prototype", "early", "production")
_DEFAULT_LEVEL = "production"

_HEADING_RE = re.compile(r"^##\s+Project Maturity\s*$", re.IGNORECASE)
_ANY_HEADING_RE = re.compile(r"^##\s")
_LEVEL_WORD_RE = re.compile(r"\b(?:" + "|".join(_LEVELS) + r")\b", re.IGNORECASE)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

_EMPTY_STDIN_REASON_CODE = "empty-stdin"
_INVALID_UTF8_STDIN_REASON_CODE = "invalid-utf8-stdin"


def _err(msg: str) -> None:
    print(f"maturity-resolve: {msg}", file=sys.stderr)


def _extract_section(text: str) -> str | None:
    """Return the `## Project Maturity` section body, or None if no such
    heading is present. The body spans every line after the heading line up
    to (but not including) the next `##` heading or the end of the
    document."""
    lines = re.split(r"\r\n|\r|\n", text)
    start = None
    for i, line in enumerate(lines):
        if _HEADING_RE.match(line):
            start = i + 1
            break
    if start is None:
        return None
    body_lines: list[str] = []
    for line in lines[start:]:
        if _ANY_HEADING_RE.match(line):
            break
        body_lines.append(line)
    return "\n".join(body_lines)


def _sanitize(raw: str) -> str:
    collapsed = re.sub(r"\s+", " ", raw).strip()
    return _CONTROL_CHAR_RE.sub("", collapsed)


def resolve(text: str) -> tuple[str, str, str | None]:
    """Return (level, reason, offending_value) for an agent-instruction
    file's body. `offending_value` is None unless `reason` is
    `invalid-value`."""
    section = _extract_section(text)
    if section is None:
        return _DEFAULT_LEVEL, "section-absent", None

    match = _LEVEL_WORD_RE.search(section)
    if match is None:
        return _DEFAULT_LEVEL, "invalid-value", _sanitize(section)

    return match.group(0).lower(), "declared", None


def main(argv: list[str]) -> int:
    del argv  # no flags — the whole interface is stdin

    raw = sys.stdin.buffer.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        _err(f"stdin is not valid UTF-8: {e}")
        _err(f"reason-code: {_INVALID_UTF8_STDIN_REASON_CODE}")
        return 2

    if not text.strip():
        _err("stdin is empty")
        _err(f"reason-code: {_EMPTY_STDIN_REASON_CODE}")
        return 2

    level, reason, offending_value = resolve(text)

    print(f"level: {level}")
    print(f"reason: {reason}")
    if offending_value is not None:
        print(f"offending-value: {offending_value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
