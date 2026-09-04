"""Tests for the candidate-set derivation gate.

The gate reads a spec body on stdin, derives its declared criteria via the
sibling `covers_gate.py`'s `parse_criteria`, derives what the spec's
`## Slices` ledger records as covered, and prints the candidate set (criteria
minus covered) plus whether the coverage union is complete enough to certify
loop termination.

Fixtures are synthetic spec bodies under `tests/fixtures/` — never real vault
records.

Exit-code contract:
  0 → derived cleanly, token block on stdout
  1 → integrity violation: a coverage token names an identifier the spec does
      not declare (`reason-code: undeclared-covered-identifier`)
  2 → fail-closed: empty/non-UTF-8 stdin, no `## Acceptance Criteria` heading,
      zero criterion identifiers (`reason-code: zero-criterion-identifiers`),
      or a malformed coverage token (`reason-code: malformed-coverage-token`)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "craft" / "scripts"
GATE = SCRIPTS_DIR / "candidate_set.py"
FIXTURES = Path(__file__).parent / "fixtures"

TWO_LINES_TWO_COVERED = (FIXTURES / "spec_candidate_two_lines_two_covered.md").read_text(
    encoding="utf-8"
)
FULL_COVERAGE = (FIXTURES / "spec_candidate_full_coverage.md").read_text(encoding="utf-8")
FULL_COVERAGE_WITH_LEGACY_LINE = (
    FIXTURES / "spec_candidate_full_coverage_with_legacy_line.md"
).read_text(encoding="utf-8")
UNION_DISJOINT = (FIXTURES / "spec_candidate_union_disjoint.md").read_text(encoding="utf-8")
EMPTY_SLICES_SECTION = (FIXTURES / "spec_candidate_empty_slices_section.md").read_text(
    encoding="utf-8"
)
NO_SLICES_SECTION = (FIXTURES / "spec_candidate_no_slices_section.md").read_text(
    encoding="utf-8"
)
UNDECLARED_COVERAGE = (FIXTURES / "spec_candidate_undeclared_coverage.md").read_text(
    encoding="utf-8"
)
MALFORMED_TOKEN = (FIXTURES / "spec_candidate_malformed_token.md").read_text(encoding="utf-8")
DUPLICATE_TOKEN = (FIXTURES / "spec_candidate_duplicate_token.md").read_text(encoding="utf-8")
MISSING_HEADING_SPEC = (FIXTURES / "spec_missing_ac_heading.md").read_text(encoding="utf-8")
ZERO_CRITERIA_SPEC = (FIXTURES / "spec_zero_criteria.md").read_text(encoding="utf-8")
FORGED_FENCE = (FIXTURES / "spec_candidate_forged_slices_in_fence.md").read_text(
    encoding="utf-8"
)
FORGED_UNCLOSED_FENCE = (
    FIXTURES / "spec_candidate_forged_slices_unclosed_fence.md"
).read_text(encoding="utf-8")
FORGED_LINE_SEPARATOR = (
    FIXTURES / "spec_candidate_forged_slices_via_line_separator.md"
).read_text(encoding="utf-8")
INLINE_MENTION = (FIXTURES / "spec_candidate_inline_slices_mention.md").read_text(
    encoding="utf-8"
)
WRAPPED_ENTRIES = (FIXTURES / "spec_candidate_wrapped_ledger_entries.md").read_text(
    encoding="utf-8"
)
NONCANONICAL_BULLET_WITH_FULL_COVERAGE = (
    FIXTURES / "spec_candidate_noncanonical_bullet_with_full_coverage.md"
).read_text(encoding="utf-8")
NUMBERED_MARKER_BEFORE_ENTRY = (
    FIXTURES / "spec_candidate_numbered_marker_before_canonical_entry.md"
).read_text(encoding="utf-8")
NUMBERED_MARKER_AFTER_ENTRY = (
    FIXTURES / "spec_candidate_numbered_marker_after_canonical_entry.md"
).read_text(encoding="utf-8")
TRAILING_PROSE_LOSES_ENTRY_COVERAGE = (
    FIXTURES / "spec_candidate_trailing_prose_loses_entry_coverage.md"
).read_text(encoding="utf-8")
HTML_COMMENT_FORGES_COMPLETE_CERTIFICATION = (
    FIXTURES / "spec_candidate_html_comment_forges_complete_certification.md"
).read_text(encoding="utf-8")
DUPLICATE_SLICES_HEADING = (
    FIXTURES / "spec_candidate_duplicate_slices_heading.md"
).read_text(encoding="utf-8")
HTML_COMMENT_HIDES_DECOY_SLICES = (
    FIXTURES / "spec_candidate_html_comment_hides_decoy_slices.md"
).read_text(encoding="utf-8")
COMPOSED_COMMENT_HIDES_DUPLICATE_SLICES_HEADING = (
    FIXTURES / "spec_candidate_composed_comment_hides_duplicate_slices_heading.md"
).read_text(encoding="utf-8")
DUPLICATE_AC_AND_SLICES_HEADINGS = (
    FIXTURES / "spec_duplicate_ac_and_slices_headings.md"
).read_text(encoding="utf-8")
PARTIAL_ONLY = (FIXTURES / "spec_candidate_partial_only.md").read_text(encoding="utf-8")
FULL_AND_PARTIAL_SAME_ENTRY = (
    FIXTURES / "spec_candidate_full_and_partial_same_entry.md"
).read_text(encoding="utf-8")
PARTIAL_THEN_FULL = (FIXTURES / "spec_candidate_partial_then_full.md").read_text(
    encoding="utf-8"
)
FULL_THEN_PARTIAL = (FIXTURES / "spec_candidate_full_then_partial.md").read_text(
    encoding="utf-8"
)
LEGACY_WITH_MODERN_PARTIAL = (
    FIXTURES / "spec_candidate_legacy_with_modern_partial.md"
).read_text(encoding="utf-8")
UNDECLARED_PARTIAL_COVERAGE = (
    FIXTURES / "spec_candidate_undeclared_partial_coverage.md"
).read_text(encoding="utf-8")
MALFORMED_PARTIAL_TOKEN = (
    FIXTURES / "spec_candidate_malformed_partial_token.md"
).read_text(encoding="utf-8")
WRAPPED_LEDGER_PARTIAL = (
    FIXTURES / "spec_candidate_wrapped_ledger_partial.md"
).read_text(encoding="utf-8")
FORGED_PARTIAL_IN_FENCE = (
    FIXTURES / "spec_candidate_forged_partial_in_fence.md"
).read_text(encoding="utf-8")
FORGED_PARTIAL_IN_HTML_COMMENT = (
    FIXTURES / "spec_candidate_forged_partial_in_html_comment.md"
).read_text(encoding="utf-8")
TORN_APPEND_PARTIAL = (FIXTURES / "spec_candidate_torn_append_partial.md").read_text(
    encoding="utf-8"
)

_UNDECLARED_REASON_CODE = "reason-code: undeclared-covered-identifier"
_MALFORMED_REASON_CODE = "reason-code: malformed-coverage-token"
_ZERO_CRITERIA_REASON_CODE = "reason-code: zero-criterion-identifiers"


def _run(spec_body: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE)],
        input=spec_body,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
    )


def _tokens(stdout: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


# ---- 1. two ledger lines, two identifiers covered -------------------------


def test_two_ledger_lines_yield_remaining_seven_candidates():
    r = _run(TWO_LINES_TWO_COVERED)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] == "AC3, AC4, AC5, AC6, AC7, AC8, AC9"
    assert tokens["complete-eligible"] == "yes"


# ---- 2. full coverage terminates the loop ----------------------------------


def test_full_coverage_yields_no_candidates_and_eligible():
    r = _run(FULL_COVERAGE)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] == "none"
    assert tokens["complete-eligible"] == "yes"


# ---- 3. full coverage but one legacy line blocks the eligibility claim ----


def test_full_coverage_with_one_legacy_line_is_not_eligible():
    r = _run(FULL_COVERAGE_WITH_LEGACY_LINE)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] == "none"
    assert tokens["complete-eligible"] == "no"


# ---- 4. coverage is a union across lines, not latest-wins ------------------


def test_coverage_is_the_union_across_disjoint_ledger_lines():
    r = _run(UNION_DISJOINT)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["covered"] == "AC1, AC3, AC2, AC4" or set(
        tokens["covered"].split(", ")
    ) == {"AC1", "AC2", "AC3", "AC4"}
    assert set(tokens["candidates"].split(", ")) == {"AC5", "AC6", "AC7", "AC8", "AC9"}
    assert tokens["complete-eligible"] == "yes"


# ---- 5. no ledger at all, or an empty one, still yields every criterion ----


def test_empty_slices_section_yields_every_criterion_as_candidate():
    r = _run(EMPTY_SLICES_SECTION)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] == "AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9"
    assert tokens["complete-eligible"] == "yes"


def test_no_slices_section_yields_every_criterion_as_candidate():
    r = _run(NO_SLICES_SECTION)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] == "AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9"
    assert tokens["complete-eligible"] == "yes"


# ---- 6. undeclared coverage identifier is an integrity violation ----------


def test_undeclared_coverage_identifier_exits_1_with_reason_code():
    r = _run(UNDECLARED_COVERAGE)
    assert r.returncode == 1, r.stderr + r.stdout
    assert _UNDECLARED_REASON_CODE in r.stderr, r.stderr
    assert "AC99" in r.stderr


# ---- 7. malformed coverage token fails closed ------------------------------


def test_malformed_coverage_token_exits_2_with_reason_code():
    r = _run(MALFORMED_TOKEN)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _MALFORMED_REASON_CODE in r.stderr, r.stderr


def test_malformed_coverage_token_remedy_names_the_token_not_a_nonexistent_flag():
    """This gate has no `--covers` flag, so its stderr must never point an
    operator at one — even though the underlying validation is reused from
    `covers_gate.py`, whose own message does name that flag correctly for its
    own CLI."""
    r = _run(MALFORMED_TOKEN)
    assert r.returncode == 2, r.stderr + r.stdout
    assert "--covers" not in r.stderr, r.stderr
    assert "ledger coverage token" in r.stderr, r.stderr


def test_within_entry_duplicate_coverage_identifier_is_malformed():
    r = _run(DUPLICATE_TOKEN)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _MALFORMED_REASON_CODE in r.stderr, r.stderr


# ---- 8. no heading / zero identifiers fail closed --------------------------


def test_missing_acceptance_criteria_heading_exits_2():
    r = _run(MISSING_HEADING_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout


def test_zero_criterion_identifiers_exits_2_with_reason_code():
    r = _run(ZERO_CRITERIA_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _ZERO_CRITERIA_REASON_CODE in r.stderr, r.stderr


# ---- 9. empty stdin fails closed -------------------------------------------


def test_empty_stdin_exits_2():
    r = _run("")
    assert r.returncode == 2, r.stderr + r.stdout


# ---- 10. forged-structure class: mandatory, not optional -------------------


def test_forged_slices_ledger_inside_fenced_block_is_invisible():
    """The fake `## Slices` heading and fully-covering line sit inside a
    fenced worked example; the real (empty) ledger below it must be what the
    gate reads, so the fenced forgery must not produce `candidates: none`."""
    r = _run(FORGED_FENCE)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] != "none"
    assert tokens["candidates"] == "AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9"


def test_forged_slices_ledger_behind_unclosed_fence_is_invisible():
    """The fence inside `## Slices` opens and never closes, so masking must
    extend to the end of the document. A masker that stops at EOF without
    keeping the fence open would let the forged full-coverage entry inside
    it be read as a real ledger entry — the mutation this pins against is
    disabling fenced-block masking entirely (`fenced = [False] * len(lines)`),
    which turns this fixture's `candidates: AC1..AC9` into a false
    `candidates: none, complete-eligible: yes`."""
    r = _run(FORGED_UNCLOSED_FENCE)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] != "none"
    assert tokens["candidates"] == "AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9"
    assert tokens["complete-eligible"] == "yes"


def test_forged_slices_ledger_behind_line_separator_is_invisible():
    """The fake heading and line are hidden behind U+2028 inside one ordinary
    prose paragraph, not a real CommonMark line break. A parser that treats
    U+2028 as a line break would anchor on the fake heading and wrongly
    report the spec fully covered."""
    r = _run(FORGED_LINE_SEPARATOR)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] != "none"
    assert tokens["candidates"] == "AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9"


def test_inline_mid_sentence_slices_mention_does_not_anchor():
    """`## Slices` mentioned inline, mid-sentence, is not a heading match at
    line start, so the fake ledger line that follows it must not be read as
    coverage — only the real, empty `## Slices` section at the end counts."""
    r = _run(INLINE_MENTION)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] == "AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9"
    assert tokens["complete-eligible"] == "yes"


# ---- 11a. logical entries, not physical lines: the real vault ledger shape -------


def test_wrapped_ledger_entries_are_scored_as_logical_entries():
    """Mirrors the real vault ledger shape: each entry wraps its value-claim prose
    across several physical lines, with the trailing parenthetical on its own
    continuation line. Scoring physical lines instead of logical entries loses
    every wrapped entry's coverage entirely — the defect this pins against."""
    r = _run(WRAPPED_ENTRIES)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert set(tokens["covered"].split(", ")) == {"AC1", "AC2", "AC5", "AC7"}, tokens
    assert tokens["candidates"] == "AC3, AC4, AC6, AC8, AC9"
    assert tokens["complete-eligible"] == "yes"


# ---- 11b. a non-canonical bullet marker forces ineligibility, fail-closed --------


def test_noncanonical_bullet_marker_forces_ineligible_despite_full_literal_coverage():
    """A ledger entry marked with the wrong bullet character is invisible to the
    canonical bullet regex. The eligibility rule must still catch it rather than
    silently reporting the coverage union complete."""
    r = _run(NONCANONICAL_BULLET_WITH_FULL_COVERAGE)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] == "none"
    assert tokens["complete-eligible"] == "no"


# ---- 11b2. a numbered-marker line forces ineligibility regardless of position ----


def test_numbered_marker_before_canonical_entry_forces_ineligible():
    """A numbered-list-marked line sitting BEFORE the section's only canonical
    entry must still force ineligibility — the eligibility rule must not be
    fooled by position, only by whether the section holds a canonical entry
    or attempted-entry content that isn't one."""
    r = _run(NUMBERED_MARKER_BEFORE_ENTRY)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] == "none", (
        "the canonical entry still covers every criterion literally: " f"{tokens}"
    )
    assert tokens["complete-eligible"] == "no", (
        "a numbered-marker line before the canonical entry must still block "
        f"eligibility, the same as one placed after it: {tokens}"
    )


def test_numbered_marker_after_canonical_entry_forces_ineligible():
    """The same numbered-marker content, placed AFTER the canonical entry —
    pins the mirror direction with a numbered marker specifically, since the
    existing after-position fixture uses an asterisk bullet instead."""
    r = _run(NUMBERED_MARKER_AFTER_ENTRY)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["candidates"] == "none"
    assert tokens["complete-eligible"] == "no"


# ---- 11b3. unmarked trailing prose swallows the preceding entry, fail-closed ----


def test_trailing_prose_after_an_entry_loses_its_coverage_but_stays_fail_closed():
    """Unmarked prose between two entries (an operator note, a sub-heading) is
    read as an ordinary continuation line of the entry above it — this is the
    same mechanism that lets a wrapped value claim's trailing parenthetical
    land on its own continuation line, so it cannot be special-cased away
    without breaking that. The affected entry's own coverage is dropped
    entirely, but the union is reported ineligible rather than fabricated
    complete: the second entry's coverage is still recognized, and eligible
    is false because the first entry no longer parses."""
    r = _run(TRAILING_PROSE_LOSES_ENTRY_COVERAGE)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["covered"] == "AC3, AC4", (
        "the first entry's AC1, AC2 coverage must be lost once trailing "
        f"prose pushes its parenthetical off the end of the entry text: {tokens}"
    )
    assert tokens["complete-eligible"] == "no"


# ---- 11c. non-UTF-8 stdin fails closed -------------------------------------


def test_non_utf8_stdin_exits_2():
    """The invalid byte sits inside an otherwise well-formed spec body (a real
    `## Acceptance Criteria` heading with declared criteria), so the only way
    this can fail closed is the UTF-8 decode itself — a body that fails for
    the missing-heading reason instead would pass this test for the wrong
    reason."""
    body = FULL_COVERAGE.encode("utf-8")
    mutated = body.replace(b"AC1", b"AC1\xff\xfe", 1)
    r = subprocess.run(
        [sys.executable, str(GATE)],
        input=mutated,
        capture_output=True,
    )
    stderr = r.stderr.decode(errors="replace")
    assert r.returncode == 2, stderr + r.stdout.decode(errors="replace")
    assert "not valid UTF-8" in stderr, stderr


# ---- 11. sibling import resolves regardless of the caller's cwd -----------


def test_sibling_import_resolves_from_an_unrelated_cwd(tmp_path):
    r = _run(FULL_COVERAGE, cwd=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "ModuleNotFoundError" not in r.stderr


# ---- 13. a symlinked entry point stays sibling-import-safe -----------------


def test_symlinked_entry_point_still_resolves_the_sibling_import(tmp_path):
    link = tmp_path / "candidate_set_link.py"
    link.symlink_to(GATE)
    r = subprocess.run(
        [sys.executable, str(link)],
        input=FULL_COVERAGE,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr + r.stdout
    assert "ModuleNotFoundError" not in r.stderr


# ---- 14. HTML comment blindness: a payload hidden in a comment must never
# forge a complete-eligible certification --------------------------------


def test_html_comment_forged_structure_never_certifies_complete_eligible():
    """The fixture's only `## Acceptance Criteria` heading, its only
    criterion, and its only `## Slices` entry live inside an HTML comment —
    invisible in every rendered view. A comment-blind gate anchors on the
    forged heading and reports a fabricated `complete-eligible: yes`. The
    gate must fail closed with no real heading found."""
    r = _run(HTML_COMMENT_FORGES_COMPLETE_CERTIFICATION)
    assert r.returncode == 2, r.stderr + r.stdout


def test_html_comment_hides_decoy_slices_entry_without_shadowing_the_real_one():
    """A decoy `## Slices` section — claiming full coverage — lives inside a
    comment above the real section. The masked decoy must contribute no
    coverage and must not be picked as the anchor; only the real entry's
    coverage counts."""
    r = _run(HTML_COMMENT_HIDES_DECOY_SLICES)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["covered"] == "AC1", tokens
    assert tokens["candidates"] == "AC2", tokens
    assert tokens["complete-eligible"] == "yes", tokens


# ---- 15. heading uniqueness: a second unmasked heading is fail-closed -----


def test_duplicate_unmasked_slices_heading_fails_closed_with_its_own_reason_code():
    """A visible second `## Slices` heading silently replaces the real
    ledger for a first-match-wins scanner — the benign-direction variant,
    where a duplicate section drops a real ledger entry rather than forging
    one. The gate must detect it and fail closed."""
    r = _run(DUPLICATE_SLICES_HEADING)
    assert r.returncode == 2, r.stderr + r.stdout
    assert "reason-code: duplicate-slices-heading" in r.stderr, r.stderr


def test_duplicate_ac_heading_fails_closed_before_reaching_the_ledger():
    """A spec with both a duplicate `## Acceptance Criteria` heading and a
    duplicate `## Slices` heading must fail closed on the criteria parse
    (shared with `covers_gate.py`) — this is the exact repro chaining both
    findings into one document."""
    r = _run(DUPLICATE_AC_AND_SLICES_HEADINGS)
    assert r.returncode == 2, r.stderr + r.stdout
    assert "reason-code: duplicate-acceptance-criteria-heading" in r.stderr, r.stderr


def test_composed_html_comment_hides_its_own_internal_duplicate_slices_heading():
    """The two findings chained: an HTML comment hides a decoy section that
    itself contains a duplicate `## Slices` heading. Both decoy occurrences
    are masked, so the single real `## Slices` section outside the comment
    remains unique — the gate must derive normally, not report a false
    duplicate and not count either decoy's coverage."""
    r = _run(COMPOSED_COMMENT_HIDES_DUPLICATE_SLICES_HEADING)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["covered"] == "AC1", tokens
    assert tokens["candidates"] == "AC2", tokens
    assert tokens["complete-eligible"] == "yes", tokens


# ---- 16. partial coverage: a criterion covered only partially stays a candidate --


def test_partial_only_entry_reports_partial_and_keeps_the_criterion_a_candidate():
    """An entry carrying only `partially covers AC2` puts AC2 on the `partial:`
    line and leaves it in `candidates:` — this is AC6, the criterion the whole
    slice exists for."""
    r = _run(PARTIAL_ONLY)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["partial"] == "AC2", tokens
    assert "AC2" in tokens["candidates"].split(", "), tokens
    assert tokens["covered"] == "none", tokens


def test_partial_only_entry_alone_is_modern_and_stays_eligible():
    """A single entry carrying only a partial field is a modern entry for
    eligibility purposes — it alone does not force `complete-eligible: no`."""
    r = _run(PARTIAL_ONLY)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["complete-eligible"] == "yes", tokens


def test_entry_with_both_covers_and_partial_covers_reports_both_correctly():
    """An entry carrying `covers AC5, partially covers AC2` reports AC5 covered,
    AC2 partial, and AC2 still a candidate."""
    r = _run(FULL_AND_PARTIAL_SAME_ENTRY)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["covered"] == "AC5", tokens
    assert tokens["partial"] == "AC2", tokens
    assert "AC2" in tokens["candidates"].split(", "), tokens
    assert "AC5" not in tokens["candidates"].split(", "), tokens


def test_partial_then_full_over_the_same_identifier_reports_it_fully_covered():
    """Two entries, one partial and a later one full over the same identifier,
    report it fully covered and absent from both `partial:` and `candidates:`."""
    r = _run(PARTIAL_THEN_FULL)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["covered"] == "AC2", tokens
    assert tokens["partial"] == "none", tokens
    assert "AC2" not in tokens["candidates"].split(", "), tokens


def test_full_then_partial_over_the_same_identifier_produces_the_identical_result():
    """The same pair in the OPPOSITE ledger order produces the identical
    result — full wins independent of order, per the union invariant."""
    r = _run(FULL_THEN_PARTIAL)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["covered"] == "AC2", tokens
    assert tokens["partial"] == "none", tokens
    assert "AC2" not in tokens["candidates"].split(", "), tokens


def test_legacy_entry_alongside_a_modern_partial_entry_still_forces_ineligible():
    """A ledger with a legacy entry (neither field) still reports
    `complete-eligible: no`, even alongside a modern partial-only entry."""
    r = _run(LEGACY_WITH_MODERN_PARTIAL)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["complete-eligible"] == "no", tokens


def test_partial_prints_none_when_no_entry_carries_the_field():
    """`partial:` prints `none` when no entry carries the field."""
    r = _run(TWO_LINES_TWO_COVERED)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["partial"] == "none", tokens


def test_partial_token_naming_undeclared_identifier_exits_1():
    """A partial token naming an undeclared identifier exits 1 with
    `reason-code: undeclared-covered-identifier`, matching the full-coverage
    path."""
    r = _run(UNDECLARED_PARTIAL_COVERAGE)
    assert r.returncode == 1, r.stderr + r.stdout
    assert _UNDECLARED_REASON_CODE in r.stderr, r.stderr
    assert "AC99" in r.stderr


def test_malformed_partial_token_exits_2():
    """A malformed partial token exits 2 with `reason-code:
    malformed-coverage-token`."""
    r = _run(MALFORMED_PARTIAL_TOKEN)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _MALFORMED_REASON_CODE in r.stderr, r.stderr


def test_wrapped_ledger_entries_with_partial_fields_are_scored_as_logical_entries():
    """Fixture the WRAPPED ledger shape, not only the tidy single-line one — a
    partial-coverage fixture that wraps its value claim across several
    physical lines, with the trailing parenthetical on a continuation line."""
    r = _run(WRAPPED_LEDGER_PARTIAL)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["covered"] == "AC5", tokens
    assert set(tokens["partial"].split(", ")) == {"AC2", "AC7"}, tokens
    assert tokens["complete-eligible"] == "yes", tokens


def test_forged_partial_token_inside_fenced_block_contributes_nothing():
    """A partial token inside a fenced code block contributes nothing — only
    the real ledger entry outside it counts."""
    r = _run(FORGED_PARTIAL_IN_FENCE)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["partial"] == "AC2", tokens
    assert tokens["covered"] == "none", tokens
    assert tokens["candidates"] == "AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9", tokens


def test_forged_partial_token_inside_html_comment_contributes_nothing():
    """A partial token inside an HTML comment contributes nothing — only the
    real ledger entry outside the comment counts."""
    r = _run(FORGED_PARTIAL_IN_HTML_COMMENT)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["partial"] == "AC1", tokens
    assert tokens["covered"] == "none", tokens
    assert tokens["candidates"] == "AC1, AC2", tokens


def test_torn_append_over_a_partial_entry_under_reports_rather_than_fabricates():
    """A ledger whose entries interleave as a concurrent double-append would —
    a partial-covering entry torn by an unmarked interleaved line — still
    fails closed under the dual-field shape: it under-reports coverage rather
    than fabricating it. The torn entry's own partial coverage is lost, but
    the second entry's full coverage still counts."""
    r = _run(TORN_APPEND_PARTIAL)
    assert r.returncode == 0, r.stderr + r.stdout
    tokens = _tokens(r.stdout)
    assert tokens["covered"] == "AC3", tokens
    assert tokens["partial"] == "none", tokens
    assert "AC2" in tokens["candidates"].split(", "), tokens
    assert tokens["complete-eligible"] == "no", tokens
