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

Criterion identifiers, the CommonMark line splitter, and the fenced-block
masker are all imported from the sibling `covers_gate.py` — never re-derived
here, so the two gates cannot disagree about what a criterion is or about what
counts as document structure. The ledger scan below is this gate's only new
parsing, and it reads through those same primitives: lines are split on the
CommonMark line grammar only (`\\r\\n`, `\\r`, `\\n` — never Python's broader
`str.splitlines()`), fenced code blocks (``` or ~~~, any info string) are
invisible to both the heading search and the ledger scan, and the `## Slices`
heading is anchored at line start, case-insensitively, so an inline
mid-sentence mention does not satisfy it.

A `## Slices` ledger entry's coverage token is the fifth field of its trailing
parenthetical:

    - **<title>** — <value claim>. (`task/<id>`, closed <date>, covers AC2, AC5)

An entry is scored as a whole — a `- ` bullet plus every continuation line up
to the next entry or the end of the section — never one physical line at a
time. The stored ledger shape wraps a long value claim across several
physical lines, with the trailing parenthetical landing on a continuation
line of its own; scoring physical lines instead of logical entries would read
every wrapped entry's continuation lines, including its coverage token, as
unrelated content and lose its coverage outright.

An entry with no coverage token (predates the field) contributes no
identifier to the covered set and makes the coverage union unverifiable as
complete — it is a legacy entry, never a fabricated full-coverage claim. So
does a line inside `## Slices` that reads as an attempted ledger entry but
does not match the canonical top-level `- ` bullet — indented, or marked with
`* ` instead of `- ` — since the bullet regex misses it entirely: this makes
the union unverifiable too, the same as a legacy entry, rather than leaving
it invisible to the eligibility rule. A spec with no `## Slices` section, or
one with no ledger entries yet, is an empty ledger: no coverage, and
eligible, since there is nothing left unaccounted for.

Stdout on success (exit 0), one token per line, in this order:

    criteria: AC1, AC2, ..., AC9
    covered: AC1, AC2
    candidates: AC3, ..., AC9
    complete-eligible: yes

`covered:` and `candidates:` print `none` when empty. `complete-eligible:` is
`yes` only when every ledger entry carries a coverage token and every entry
in the section matched the canonical bullet shape; a single legacy entry, or
a single non-canonical bullet marker, makes it `no`, because the union is
then known to be incomplete or unverifiable and no caller may report the
spec complete on it.

A within-entry duplicate identifier (`covers AC1, AC1`) is rejected the same
way `covers_gate.py` rejects a duplicate in a drafted `--covers` value,
because both go through the shared `parse_covers`: fail-closed as a
malformed coverage token, not silently deduplicated.

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
       `reason-code: zero-criterion-identifiers`); or a ledger entry whose
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
    _COMMONMARK_LINE_RE,
    _ZERO_CRITERIA_REASON_CODE,
    _mask_fenced_lines,
    parse_covers,
    parse_criteria,
)

_SLICES_HEADING_RE = re.compile(r"^## Slices$", re.IGNORECASE)
_LEDGER_BULLET_RE = re.compile(r"^- ")
_LEDGER_NONCANONICAL_MARKER_RE = re.compile(r"^\s*[-*]\s")
_LEDGER_TRAILING_PAREN_RE = re.compile(r"\(([^()]*)\)\s*$")
_LEDGER_FIELDS_RE = re.compile(r"^`task/[^`]+`, closed [^,]+(?:, covers (?P<covers>.+))?$")

_UNDECLARED_REASON_CODE = "undeclared-covered-identifier"
_MALFORMED_TOKEN_REASON_CODE = "malformed-coverage-token"


class UndeclaredCoverageError(ValueError):
    """A ledger coverage token names an identifier the spec never declares."""


class MalformedCoverageTokenError(ValueError):
    """A ledger coverage token does not parse as an ACn identifier list."""


def _err(msg: str) -> None:
    print(f"candidate-set: {msg}", file=sys.stderr)


def parse_ledger(spec_body: str, criteria: list[str]) -> tuple[list[str], bool]:
    """Return (covered identifiers in first-seen order, eligible).

    A ledger entry begins at a top-level `- ` bullet within `## Slices` and
    continues through its continuation lines until the next entry begins or
    the section ends — an entry is scored as a whole, never one physical
    line at a time, so a value claim that wraps across several physical
    lines (the stored ledger shape) is assembled before its trailing
    parenthetical is read.

    `eligible` is True only when every entry found carries a coverage token
    and the section holds no other non-blank content. A line that reads as
    an attempted ledger entry but does not match the canonical `- ` bullet
    (indented, or marked `* `) is exactly such content: the bullet regex
    misses it, so it never becomes an entry, but its presence still makes
    the coverage union unverifiable and is fail-closed here rather than
    silently ignored. A spec with no `## Slices` section, or one with no
    ledger entries, is an empty ledger: no coverage, eligible.

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
    entry_lines: list[str] = []

    def finalize_entry() -> None:
        nonlocal eligible
        if not entry_lines:
            return
        text = " ".join(l.strip() for l in entry_lines)
        paren = _LEDGER_TRAILING_PAREN_RE.search(text)
        if paren is None:
            eligible = False
            return
        fields = _LEDGER_FIELDS_RE.match(paren.group(1))
        if fields is None or fields.group("covers") is None:
            eligible = False
            return
        try:
            identifiers = parse_covers(fields.group("covers"))
        except ValueError as e:
            raise MalformedCoverageTokenError(
                str(e).replace("--covers value", "ledger coverage token")
            ) from e
        for identifier in identifiers:
            if identifier not in criteria:
                raise UndeclaredCoverageError(identifier)
            if identifier not in seen:
                seen.add(identifier)
                covered.append(identifier)

    in_noncanonical = False
    for i in range(start, len(lines)):
        if fenced[i]:
            continue
        line = lines[i]
        if line.startswith("## "):
            break
        if not line.strip():
            continue
        if _LEDGER_BULLET_RE.match(line):
            finalize_entry()
            entry_lines = [line]
            in_noncanonical = False
            continue
        if _LEDGER_NONCANONICAL_MARKER_RE.match(line):
            finalize_entry()
            entry_lines = []
            eligible = False
            in_noncanonical = True
            continue
        if in_noncanonical or not entry_lines:
            # Either the continuation of an already-flagged non-canonical
            # entry, or ordinary prose with no bullet open yet (e.g. a
            # placeholder line before the first slice ships) — neither is
            # ledger structure, so neither contributes to an entry.
            continue
        entry_lines.append(line)
    finalize_entry()
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
