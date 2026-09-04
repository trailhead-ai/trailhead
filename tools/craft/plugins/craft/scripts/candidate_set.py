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

Criterion identifiers, the CommonMark line splitter, the fenced-block-and
-HTML-comment masker, and the heading-uniqueness check are all imported from
the sibling `covers_gate.py` — never re-derived here, so the two gates cannot
disagree about what a criterion is or about what counts as document
structure. The ledger scan below is this gate's only new parsing, and it
reads through those same primitives: lines are split on the CommonMark line
grammar only (`\\r\\n`, `\\r`, `\\n` — never Python's broader
`str.splitlines()`); fenced code blocks (``` or ~~~, any info string) and
CommonMark HTML comments (`<!-- ... -->`, to end of document if never
closed) are invisible to both the heading search and the ledger scan; the
`## Slices` heading is anchored at line start, case-insensitively, so an
inline mid-sentence mention does not satisfy it; and a second unmasked
`## Slices` heading is a fail-closed integrity error rather than a silent
substitution of one ledger for another.

A `## Slices` ledger entry's coverage tokens are the trailing fields of its
parenthetical, after `task/<id>` and `closed <date>`: an optional `covers`
field, an optional `partially covers` field, or both:

    - **<title>** — <value claim>. (`task/<id>`, closed <date>, covers AC2, AC5)
    - **<title>** — <value claim>. (`task/<id>`, closed <date>, partially covers AC7)
    - **<title>** — <value claim>. (`task/<id>`, closed <date>, covers AC5, partially covers AC2)

The `partially covers` field is split off the parenthetical text BEFORE the
`covers` field's regex is applied to what remains — appending an optional
group to the existing `covers` pattern would let its greedy match swallow
the trailing `partially covers` field instead of stopping before it. Both
fields go through the same `parse_covers` used for the drafted
`--covers`/`--partial-covers` flags in `covers_gate.py`, so a malformed or
undeclared identifier in either field is rejected identically.

An entry is scored as a whole — a `- ` bullet plus every continuation line up
to the next entry or the end of the section — never one physical line at a
time. The stored ledger shape wraps a long value claim across several
physical lines, with the trailing parenthetical landing on a continuation
line of its own; scoring physical lines instead of logical entries would read
every wrapped entry's continuation lines, including its coverage token, as
unrelated content and lose its coverage outright. The same mechanism that
makes a wrapped value claim's coverage findable also means unmarked prose
between two entries (an operator note, a stray sub-heading) is read as a
continuation line of the entry above it: it pushes that entry's trailing
parenthetical off the end of the joined text, so the whole entry fails to
parse and that entry's own coverage is dropped. This is fail-closed, not
unsafe — the union is reported ineligible rather than fabricated complete —
but it means an entry can lose its coverage even though nothing else about
it changed, simply because trailing prose followed it in the section.

An entry with neither coverage token (predates both fields) contributes no
identifier to either set and makes the coverage union unverifiable as
complete — it is a legacy entry, never a fabricated full-coverage claim. An
entry carrying either field, or both, is a modern entry for eligibility
purposes: only an entry carrying neither keeps `complete-eligible: no`. Full
coverage wins over partial for the same identifier regardless of which
ledger line carries which field — the union across lines is order-free, so a
later full-coverage entry resolves an earlier partial claim on the same
identifier the same way an earlier full-coverage entry does. So
does a line inside `## Slices` that reads as an attempted ledger entry but
does not match the canonical top-level `- ` bullet — indented, marked with
`* ` instead of `- `, or using a numbered-list marker (`1.`, `2)`) — since the
bullet regex misses it entirely: this makes the union unverifiable too, the
same as a legacy entry, rather than leaving it invisible to the eligibility
rule, and it does so regardless of whether that line sits before or after
the canonical entries in the section. A spec with no `## Slices` section, or
one with no ledger entries yet, is an empty ledger: no coverage, and
eligible, since there is nothing left unaccounted for.

Stdout on success (exit 0), one token per line, in this order:

    criteria: AC1, AC2, ..., AC9
    covered: AC1, AC2
    candidates: AC3, ..., AC9
    partial: AC7
    complete-eligible: yes

`covered:`, `candidates:`, and `partial:` print `none` when empty. `candidates:`
is computed against the fully covered set alone — a criterion covered only
partially remains a candidate on every subsequent pass, and it is never
listed on `partial:` once a later entry fully covers it. `complete-eligible:`
is `yes` only when every ledger entry carries at least one coverage token
(`covers`, `partially covers`, or both) and every non-blank line in the
section either belongs to a canonical entry (its bullet or one of its
continuation lines) or is blank; a single legacy entry (neither field), or a
single non-canonical marker line — wherever in the section it sits — makes it
`no`, because the union is then known to be incomplete or unverifiable and no
caller may report the spec complete on it.

A within-entry duplicate identifier (`covers AC1, AC1`) is rejected the same
way `covers_gate.py` rejects a duplicate in a drafted `--covers` value,
because both go through the shared `parse_covers`: fail-closed as a
malformed coverage token, not silently deduplicated. This applies to the
`partially covers` field identically.

A `reason:` line printed to stderr may echo raw ledger prose read from the
spec body. It is for a human reading stderr and is never persisted — only the
bounded `reason-code:` token below it may be copied into a record, a task
body, or a commit message.

Exit codes:
    0  derived cleanly — the token block above is on stdout.
    1  integrity violation — a `covers` or `partially covers` token names an
       identifier the spec does not declare under `## Acceptance Criteria`
       (`reason-code: undeclared-covered-identifier`).
    2  fail-closed — empty or non-UTF-8 stdin; no `## Acceptance Criteria`
       heading; a spec declaring zero criterion identifiers under that
       heading (reuses `covers_gate.py`'s
       `reason-code: zero-criterion-identifiers`); a second unmasked
       `## Acceptance Criteria` heading (reuses `covers_gate.py`'s
       `reason-code: duplicate-acceptance-criteria-heading`); a second
       unmasked `## Slices` heading
       (`reason-code: duplicate-slices-heading`); or a `covers` or
       `partially covers` token that does not parse as an identifier list
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
    DuplicateHeadingError,
    _find_unique_heading,
    _mask_fenced_lines,
    parse_covers,
    parse_criteria,
)

_SLICES_HEADING = "## Slices"
_SLICES_HEADING_RE = re.compile(r"^## Slices$", re.IGNORECASE)
_LEDGER_BULLET_RE = re.compile(r"^- ")
_LEDGER_NONCANONICAL_MARKER_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s")
_LEDGER_TRAILING_PAREN_RE = re.compile(r"\(([^()]*)\)\s*$")
_LEDGER_FIELDS_RE = re.compile(r"^`task/[^`]+`, closed [^,]+(?:, covers (?P<covers>.+))?$")
_LEDGER_PARTIAL_SPLIT_RE = re.compile(r", partially covers (?P<partial>.+)$")

_UNDECLARED_REASON_CODE = "undeclared-covered-identifier"
_MALFORMED_TOKEN_REASON_CODE = "malformed-coverage-token"
_DUPLICATE_SLICES_HEADING_REASON_CODE = "duplicate-slices-heading"


class UndeclaredCoverageError(ValueError):
    """A ledger coverage token names an identifier the spec never declares."""


class MalformedCoverageTokenError(ValueError):
    """A ledger coverage token does not parse as an ACn identifier list."""


def _err(msg: str) -> None:
    print(f"candidate-set: {msg}", file=sys.stderr)


def parse_ledger(
    spec_body: str, criteria: list[str]
) -> tuple[list[str], list[str], bool]:
    """Return (fully covered identifiers, partially covered identifiers,
    eligible) — both identifier lists in first-seen order.

    A ledger entry begins at a top-level `- ` bullet within `## Slices` and
    continues through its continuation lines until the next entry begins,
    another marker line is seen, or the section ends — an entry is scored as
    a whole, never one physical line at a time, so a value claim that wraps
    across several physical lines (the stored ledger shape) is assembled
    before its trailing parenthetical is read. That same assembly means
    unmarked prose trailing a valid entry (an operator note, a stray
    sub-heading) is folded into it as if it were a continuation line, which
    breaks that entry's own trailing-parenthetical match and drops its
    coverage — fail-closed, not fabricated, but worth knowing before
    tightening this rule further.

    `eligible` is True only when every entry found carries a coverage token
    and every non-blank line in the section either belongs to a canonical
    entry (its bullet or a continuation line) or is blank. A line that reads
    as an attempted ledger entry but does not match the canonical `- `
    bullet — indented, marked `* `, or numbered (`1.`, `2)`) — is exactly
    such content: the bullet regex misses it, so it never becomes an entry,
    but its presence still makes the coverage union unverifiable and is
    fail-closed here rather than silently ignored, whether it appears before
    the first canonical entry, between two entries, or after the last one.
    A spec with no `## Slices` section, or one with no ledger entries, is an
    empty ledger: no coverage, eligible.

    An entry's `partially covers` field is split off the joined entry text
    before `_LEDGER_FIELDS_RE` (which owns only the `covers` field) is
    applied to what remains — an optional group appended to that pattern
    instead would let its greedy `covers` match swallow a trailing
    `partially covers` field rather than stopping before it. Full coverage
    wins over partial for the same identifier regardless of which line
    carries which: the returned `partial` list is filtered against the final
    fully covered set after every entry has been scored, so the result is
    order-free across the ledger.

    Raises UndeclaredCoverageError if a `covers` or `partially covers` token
    names an identifier not in `criteria`, or MalformedCoverageTokenError if
    either does not parse as an ACn identifier list.
    """
    lines = _COMMONMARK_LINE_RE.split(spec_body)
    fenced = _mask_fenced_lines(lines)

    start = _find_unique_heading(
        lines, fenced, _SLICES_HEADING_RE, _SLICES_HEADING, _DUPLICATE_SLICES_HEADING_REASON_CODE
    )
    if start is None:
        return [], [], True

    # Insertion-ordered sets: a dict keeps first-seen order while making the
    # repeat check O(1), so neither list needs a parallel `seen` set beside it.
    covered: dict[str, None] = {}
    partial: dict[str, None] = {}
    eligible = True
    entry_lines: list[str] = []

    def _parse_field(value: str) -> list[str]:
        try:
            identifiers = parse_covers(value, flag="ledger coverage token")
        except ValueError as e:
            raise MalformedCoverageTokenError(str(e)) from e
        for identifier in identifiers:
            if identifier not in criteria:
                raise UndeclaredCoverageError(identifier)
        return identifiers

    def finalize_entry() -> None:
        nonlocal eligible
        if not entry_lines:
            return
        text = " ".join(line.strip() for line in entry_lines)
        paren = _LEDGER_TRAILING_PAREN_RE.search(text)
        if paren is None:
            eligible = False
            return
        paren_text = paren.group(1)
        partial_match = _LEDGER_PARTIAL_SPLIT_RE.search(paren_text)
        if partial_match is not None:
            partial_value = partial_match.group("partial")
            head = paren_text[: partial_match.start()]
        else:
            partial_value = None
            head = paren_text
        fields = _LEDGER_FIELDS_RE.match(head)
        if fields is None or (fields.group("covers") is None and partial_value is None):
            eligible = False
            return
        if fields.group("covers") is not None:
            covered.update(dict.fromkeys(_parse_field(fields.group("covers"))))
        if partial_value is not None:
            partial.update(dict.fromkeys(_parse_field(partial_value)))

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
    return list(covered), [i for i in partial if i not in covered], eligible


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
    except DuplicateHeadingError as e:
        _err(f"reason: {e}")
        _err(f"reason-code: {e.reason_code}")
        return 2
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
        covered, partial, eligible = parse_ledger(spec_body, criteria)
    except DuplicateHeadingError as e:
        _err(f"reason: {e}")
        _err(f"reason-code: {e.reason_code}")
        return 2
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
    print(f"partial: {', '.join(partial) if partial else 'none'}")
    print(f"complete-eligible: {'yes' if eligible else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
