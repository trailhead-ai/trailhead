#!/usr/bin/env python3
"""Candidate-set gate — derives the slice loop's candidate set and its
termination eligibility from a spec's declared criteria and its `## Slices`
ledger, BEFORE a caller decides whether to select another slice or stop.

Usage:
    lore record show spec/<name> | candidate_set.py

The spec body arrives on stdin; there is no flag. This is the opposite CLI
convention from `covers_gate.py`: a gate that certifies a caller-drafted value
takes a flag, a gate that derives a value from a document alone reads stdin
only. A third gate here should pick the convention that matches which of the
two it is doing, not copy either example blindly.

Criterion identifiers are derived by importing `parse_criteria` from the
sibling `covers_gate.py` — never re-derived here, so the two gates cannot
disagree about what a criterion is. The ledger parser below is new, and
follows the same document-format discipline `covers_gate.py` already
establishes: lines are split on the CommonMark line grammar only (`\\r\\n`,
`\\r`, `\\n` — never Python's broader `str.splitlines()`), fenced code blocks
(``` or ~~~, any info string) are invisible to both the heading search and the
ledger scan, and the `## Slices` heading is anchored at line start,
case-insensitively, so an inline mid-sentence mention does not satisfy it.

A `## Slices` ledger line's coverage token is the fifth field of its trailing
parenthetical:

    - **<title>** — <value claim>. (`task/<id>`, closed <date>, covers AC2, AC5)

A line with no coverage token (predates the field) contributes no identifier
to the covered set and makes the coverage union unverifiable as complete — it
is a legacy line, never a fabricated full-coverage claim. A spec with no
`## Slices` section, or one with no ledger lines yet, is an empty ledger: no
coverage, and eligible, since there is nothing left unaccounted for.

Stdout on success (exit 0), one token per line, in this order:

    criteria: AC1, AC2, ..., AC9
    covered: AC1, AC2
    candidates: AC3, ..., AC9
    complete-eligible: yes

`covered:` and `candidates:` print `none` when empty. `complete-eligible:` is
`yes` only when every ledger line carries a coverage token; a single legacy
line makes it `no`, because the union is then known to be incomplete and no
caller may report the spec complete on it.

A `reason:` line printed to stderr may echo raw ledger prose read from the
spec body. It is for a human reading stderr and is never persisted — only the
bounded `reason-code:` token below it may be copied into a record, a task
body, or a commit message.

Exit codes:
    0  derived cleanly — the token block above is on stdout.
    1  integrity violation — a ledger coverage token names an identifier the
       spec does not declare under `## Acceptance Criteria`
       (`reason-code: undeclared-covered-identifier`).
    2  fail-closed — empty or non-UTF-8 stdin; no `## Acceptance Criteria`
       heading; a spec declaring zero criterion identifiers under that
       heading (reuses `covers_gate.py`'s
       `reason-code: zero-criterion-identifiers`); or a ledger line whose
       coverage token does not parse as an identifier list
       (`reason-code: malformed-coverage-token`). NEVER exits 0 when the
       derivation could not be certified.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from covers_gate import (  # noqa: E402
    _ZERO_CRITERIA_REASON_CODE,
    parse_covers,
    parse_criteria,
)

_SLICES_HEADING_RE = re.compile(r"^## Slices$", re.IGNORECASE)
_FENCE_START_RE = re.compile(r"^(`{3,}|~{3,})")
_LEDGER_BULLET_RE = re.compile(r"^- ")
_LEDGER_TRAILING_PAREN_RE = re.compile(r"\(([^()]*)\)\s*$")
_LEDGER_FIELDS_RE = re.compile(r"^`task/[^`]+`, closed [^,]+(?:, covers (?P<covers>.+))?$")
# CommonMark line endings only — see covers_gate.py's identical constant for
# why str.splitlines() must never be used here: it also breaks on U+2028,
# U+2029, NEL, \v, and \f, none of which render as a line break to a human
# or a CommonMark reader.
_COMMONMARK_LINE_RE = re.compile(r"\r\n|\r|\n")

_UNDECLARED_REASON_CODE = "undeclared-covered-identifier"
_MALFORMED_TOKEN_REASON_CODE = "malformed-coverage-token"


class UndeclaredCoverageError(ValueError):
    """A ledger coverage token names an identifier the spec never declares."""


class MalformedCoverageTokenError(ValueError):
    """A ledger coverage token does not parse as an ACn identifier list."""


def _err(msg: str) -> None:
    print(f"candidate-set: {msg}", file=sys.stderr)


def _mask_fenced_lines(lines: list[str]) -> list[bool]:
    """Return one boolean per line — True where the line is a fence marker
    or falls inside a fenced code block (``` or ~~~, any info string)."""
    masked = [False] * len(lines)
    fence_char: str | None = None
    fence_len = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        m = _FENCE_START_RE.match(stripped)
        if fence_char is None:
            if m:
                fence_char = m.group(1)[0]
                fence_len = len(m.group(1))
                masked[i] = True
            continue
        masked[i] = True
        if m and m.group(1)[0] == fence_char and len(m.group(1)) >= fence_len:
            fence_char = None
    return masked


def parse_ledger(spec_body: str, criteria: list[str]) -> tuple[list[str], bool]:
    """Return (covered identifiers in first-seen order, eligible).

    `eligible` is True only when every ledger line found carries a coverage
    token. A spec with no `## Slices` section, or one with no ledger lines,
    is an empty ledger: no coverage, eligible.

    Raises UndeclaredCoverageError if a coverage token names an identifier
    not in `criteria`, or MalformedCoverageTokenError if a coverage token
    does not parse as an ACn identifier list.
    """
    lines = _COMMONMARK_LINE_RE.split(spec_body)
    fenced = _mask_fenced_lines(lines)

    start = None
    for i, line in enumerate(lines):
        if fenced[i]:
            continue
        if _SLICES_HEADING_RE.match(line):
            start = i + 1
            break
    if start is None:
        return [], True

    covered: list[str] = []
    seen: set[str] = set()
    eligible = True
    for i in range(start, len(lines)):
        if fenced[i]:
            continue
        line = lines[i]
        if line.startswith("## "):
            break
        if not _LEDGER_BULLET_RE.match(line):
            continue
        paren = _LEDGER_TRAILING_PAREN_RE.search(line)
        if paren is None:
            eligible = False
            continue
        fields = _LEDGER_FIELDS_RE.match(paren.group(1))
        if fields is None or fields.group("covers") is None:
            eligible = False
            continue
        try:
            identifiers = parse_covers(fields.group("covers"))
        except ValueError as e:
            raise MalformedCoverageTokenError(str(e)) from e
        for identifier in identifiers:
            if identifier not in criteria:
                raise UndeclaredCoverageError(identifier)
            if identifier not in seen:
                seen.add(identifier)
                covered.append(identifier)
    return covered, eligible


def main(argv: list[str]) -> int:
    del argv  # no flags — the whole interface is stdin

    try:
        spec_body = sys.stdin.buffer.read().decode("utf-8")
    except UnicodeDecodeError as e:
        _err(f"spec body on stdin is not valid UTF-8: {e}")
        return 2
    if not spec_body.strip():
        _err("spec body on stdin is empty")
        return 2

    try:
        criteria = parse_criteria(spec_body)
    except ValueError as e:
        _err(str(e))
        _err("failing closed — cannot derive a candidate set without a criteria list")
        return 2

    if not criteria:
        _err(
            "reason: spec declares no criterion identifiers under "
            "'## Acceptance Criteria' — this is the legacy shape a spec predating "
            "the **ACn.** convention carries"
        )
        _err(f"reason-code: {_ZERO_CRITERIA_REASON_CODE}")
        return 2

    try:
        covered, eligible = parse_ledger(spec_body, criteria)
    except MalformedCoverageTokenError as e:
        _err(f"reason: {e}")
        _err(f"reason-code: {_MALFORMED_TOKEN_REASON_CODE}")
        return 2
    except UndeclaredCoverageError as e:
        _err(f"reason: ledger names an identifier the spec does not declare: {e}")
        _err(f"reason-code: {_UNDECLARED_REASON_CODE}")
        return 1

    candidates = [c for c in criteria if c not in covered]

    print(f"criteria: {', '.join(criteria)}")
    print(f"covered: {', '.join(covered) if covered else 'none'}")
    print(f"candidates: {', '.join(candidates) if candidates else 'none'}")
    print(f"complete-eligible: {'yes' if eligible else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
