"""Tests for the observation gate.

The gate reads a slice parent's task body on stdin, pairs every identifier in
its `**Covers:**` field against a line in its `## Criterion observations`
section, and certifies or refuses. A `manual-check` observation additionally
requires a matching `## Operator attestations` line for the same identifier.

Fixtures are synthetic parent bodies under `tests/fixtures/` — except
`parent_live_partial_coverage.md`, captured verbatim from the real closed
parent `task/partial-coverage-keeps-a-criterion-s-remainder-in-the-candidate-set`.

Exit-code contract:
  0 → derived cleanly — every covered identifier carries a well-formed
      observation (and every manual-check observation has a matching
      attestation), or there is no `**Covers:**` field at all
  1 → integrity violation: a missing, duplicated, undeclared, or malformed
      observation; a duplicated `**Covers:**` identifier; or a manual-check
      observation with no matching attestation (prints a `reason:` line)
  2 → could not certify: empty stdin (`reason-code: empty-stdin`), non-UTF-8
      stdin (`reason-code: non-utf8-stdin`), a malformed `**Covers:**` value
      (`reason-code: malformed-covers-field`), a second unmasked
      `**Covers:**` line (`reason-code: duplicate-covers-field`), a second
      unmasked `## Criterion observations` heading
      (`reason-code: duplicate-observations-section`), a second unmasked
      `## Operator attestations` heading
      (`reason-code: duplicate-attestations-section`), or the document ending
      while still inside an open fenced code block or an open HTML comment
      (`reason-code: unterminated-masked-region`) — every exit-2 case prints
      both a `reason:` and a `reason-code:` line
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
GATE = REPO_ROOT / "plugins" / "craft" / "scripts" / "observation_gate.py"
FIXTURES = Path(__file__).parent / "fixtures"

ALL_OBSERVED = (FIXTURES / "parent_all_observed.md").read_text(encoding="utf-8")
NO_COVERS = (FIXTURES / "parent_no_covers.md").read_text(encoding="utf-8")
MISSING_OBSERVATION = (FIXTURES / "parent_missing_observation.md").read_text(encoding="utf-8")
DUPLICATE_OBSERVATION = (FIXTURES / "parent_duplicate_observation.md").read_text(
    encoding="utf-8"
)
UNDECLARED_OBSERVATION = (FIXTURES / "parent_undeclared_observation.md").read_text(
    encoding="utf-8"
)
UNSANCTIONED_METHOD = (FIXTURES / "parent_unsanctioned_method.md").read_text(encoding="utf-8")
EMPTY_EVIDENCE = (FIXTURES / "parent_empty_evidence.md").read_text(encoding="utf-8")
MALFORMED_COVERS = (FIXTURES / "parent_malformed_covers.md").read_text(encoding="utf-8")
DUPLICATE_COVERS = (FIXTURES / "parent_duplicate_covers.md").read_text(encoding="utf-8")
FORGED_HEADING_IN_FENCE = (FIXTURES / "parent_forged_heading_in_fence.md").read_text(
    encoding="utf-8"
)
FORGED_OBSERVATION_IN_FENCE = (
    FIXTURES / "parent_forged_observation_in_fence.md"
).read_text(encoding="utf-8")
FORGED_OBSERVATION_IN_HTML_COMMENT = (
    FIXTURES / "parent_forged_observation_in_html_comment.md"
).read_text(encoding="utf-8")
DUPLICATE_OBSERVATIONS_HEADING = (
    FIXTURES / "parent_duplicate_observations_heading.md"
).read_text(encoding="utf-8")
MANUAL_CHECK_NO_ATTESTATION = (
    FIXTURES / "parent_manual_check_no_attestation.md"
).read_text(encoding="utf-8")
MANUAL_CHECK_WRONG_ATTESTATION = (
    FIXTURES / "parent_manual_check_wrong_attestation.md"
).read_text(encoding="utf-8")
MANUAL_CHECK_WITH_ATTESTATION = (
    FIXTURES / "parent_manual_check_with_attestation.md"
).read_text(encoding="utf-8")
LIVE_PARTIAL_COVERAGE = (FIXTURES / "parent_live_partial_coverage.md").read_text(
    encoding="utf-8"
)
DUPLICATE_COVERS_FIELD = (FIXTURES / "parent_duplicate_covers_field.md").read_text(
    encoding="utf-8"
)
COVERS_FIELD_IN_FENCE = (FIXTURES / "parent_covers_field_in_fence.md").read_text(
    encoding="utf-8"
)
MANUAL_CHECK_WHITESPACE_ATTESTATION = (
    FIXTURES / "parent_manual_check_whitespace_attestation.md"
).read_text(encoding="utf-8")
DUPLICATE_ATTESTATION = (FIXTURES / "parent_duplicate_attestation.md").read_text(
    encoding="utf-8"
)
INERT_EVIDENCE = (FIXTURES / "parent_inert_evidence.md").read_text(encoding="utf-8")
PARTIALLY_COVERS_ONLY = (FIXTURES / "parent_partially_covers_only.md").read_text(
    encoding="utf-8"
)
UNTERMINATED_FENCE = (FIXTURES / "parent_unterminated_fence.md").read_text(encoding="utf-8")
UNTERMINATED_HTML_COMMENT = (
    FIXTURES / "parent_unterminated_html_comment.md"
).read_text(encoding="utf-8")
COVERS_FIELD_LOWERCASE = (FIXTURES / "parent_covers_field_lowercase.md").read_text(
    encoding="utf-8"
)
OBSERVATION_UNDER_SUBHEADING = (
    FIXTURES / "parent_observation_under_subheading.md"
).read_text(encoding="utf-8")
COVERS_FIELD_IN_HTML_COMMENT = (
    FIXTURES / "parent_covers_field_in_html_comment.md"
).read_text(encoding="utf-8")
INVISIBLE_UNICODE_EVIDENCE = (
    FIXTURES / "parent_invisible_unicode_evidence.md"
).read_text(encoding="utf-8")
INVISIBLE_UNICODE_ATTESTATION = (
    FIXTURES / "parent_invisible_unicode_attestation.md"
).read_text(encoding="utf-8")
FULLWIDTH_IDENTIFIER = (FIXTURES / "parent_fullwidth_identifier.md").read_text(
    encoding="utf-8"
)

_MALFORMED_COVERS_REASON_CODE = "reason-code: malformed-covers-field"
_DUPLICATE_OBSERVATIONS_SECTION_REASON_CODE = "reason-code: duplicate-observations-section"
_DUPLICATE_COVERS_FIELD_REASON_CODE = "reason-code: duplicate-covers-field"
_DUPLICATE_ATTESTATIONS_SECTION_REASON_CODE = "reason-code: duplicate-attestations-section"
_UNTERMINATED_MASKED_REGION_REASON_CODE = "reason-code: unterminated-masked-region"
_EMPTY_STDIN_REASON_CODE = "reason-code: empty-stdin"
_NON_UTF8_STDIN_REASON_CODE = "reason-code: non-utf8-stdin"


def _run(body: str | bytes) -> subprocess.CompletedProcess:
    if isinstance(body, str):
        return subprocess.run(
            [sys.executable, str(GATE)], input=body, capture_output=True, text=True
        )
    return subprocess.run([sys.executable, str(GATE)], input=body, capture_output=True)


# ---- item 1: clean path -------------------------------------------------


def test_every_covered_identifier_observed_exits_0():
    result = _run(ALL_OBSERVED)
    assert result.returncode == 0, result.stderr
    assert "AC9" in result.stdout
    assert "AC7" in result.stdout
    assert "AC3" in result.stdout
    assert "automated-assertion" in result.stdout
    assert "design-doc-review" in result.stdout
    assert "manual-check" in result.stdout


# ---- item 2: no Covers field ---------------------------------------------


def test_no_covers_field_exits_0_and_reports_none():
    result = _run(NO_COVERS)
    assert result.returncode == 0, result.stderr
    assert "covers: none" in result.stdout


def test_covers_field_matches_case_insensitively():
    # `_OBSERVATIONS_HEADING_RE` and `_ATTESTATIONS_HEADING_RE` are both
    # case-insensitive; `**Covers:**` must be too, for consistency with its
    # own siblings in this module.
    result = _run(COVERS_FIELD_LOWERCASE)
    assert result.returncode == 0, result.stderr
    assert "AC9" in result.stdout


# ---- item 3: missing observation -----------------------------------------


def test_covered_identifier_with_no_observation_exits_1_naming_it():
    result = _run(MISSING_OBSERVATION)
    assert result.returncode == 1
    assert "AC9" in result.stderr
    assert "reason:" in result.stderr


# ---- item 4: duplicate observation ---------------------------------------


def test_duplicate_observation_for_one_identifier_exits_1_naming_it():
    result = _run(DUPLICATE_OBSERVATION)
    assert result.returncode == 1
    assert "AC9" in result.stderr


# ---- item 5: undeclared observation ---------------------------------------


def test_observation_for_undeclared_identifier_exits_1_naming_it():
    result = _run(UNDECLARED_OBSERVATION)
    assert result.returncode == 1
    assert "AC5" in result.stderr


# ---- item 6: unsanctioned method -------------------------------------------


def test_unsanctioned_method_token_exits_1_naming_token_and_identifier():
    result = _run(UNSANCTIONED_METHOD)
    assert result.returncode == 1
    assert "eyeballed-it" in result.stderr
    assert "AC9" in result.stderr


# ---- item 7: empty evidence -------------------------------------------------


def test_empty_evidence_exits_1_naming_identifier():
    result = _run(EMPTY_EVIDENCE)
    assert result.returncode == 1
    assert "AC9" in result.stderr


# ---- item 8: malformed Covers grammar ---------------------------------------


def test_malformed_covers_value_exits_2_with_reason_code():
    result = _run(MALFORMED_COVERS)
    assert result.returncode == 2
    assert _MALFORMED_COVERS_REASON_CODE in result.stderr


# ---- item 9: empty / non-UTF-8 stdin -----------------------------------------


def test_empty_stdin_exits_2_with_reason_code():
    # Empty stdin is the most likely exit-2 case in practice — an upstream
    # `lore record show` that produced no output — so it gets the same
    # reason:/reason-code: pair as every other exit-2 case, not silence.
    result = _run("")
    assert result.returncode == 2
    assert "reason:" in result.stderr
    assert _EMPTY_STDIN_REASON_CODE in result.stderr


def test_non_utf8_stdin_exits_2_with_reason_code():
    result = _run(b"\xff\xfe\x00\x01 not utf-8")
    stderr = result.stderr.decode("utf-8", errors="replace")
    assert result.returncode == 2
    assert "reason:" in stderr
    assert _NON_UTF8_STDIN_REASON_CODE in stderr


# ---- item 10: forged-structure class -----------------------------------------


def test_observations_heading_inside_fence_is_invisible_to_heading_search():
    # The fenced heading must not count as a second unmasked occurrence of
    # the real heading below it — if it did, the gate would fail closed with
    # a duplicate-heading error instead of certifying against the real one.
    result = _run(FORGED_HEADING_IN_FENCE)
    assert result.returncode == 0, result.stderr
    assert "AC9" in result.stdout


def test_observation_line_inside_fence_is_invisible_to_line_scan():
    result = _run(FORGED_OBSERVATION_IN_FENCE)
    assert result.returncode == 1
    assert "AC9" in result.stderr


def test_observation_line_inside_html_comment_is_invisible_to_line_scan():
    result = _run(FORGED_OBSERVATION_IN_HTML_COMMENT)
    assert result.returncode == 1
    assert "AC9" in result.stderr


def test_duplicate_unmasked_observations_heading_exits_2():
    result = _run(DUPLICATE_OBSERVATIONS_HEADING)
    assert result.returncode == 2
    assert _DUPLICATE_OBSERVATIONS_SECTION_REASON_CODE in result.stderr


def test_observation_under_subheading_still_counts():
    # A `### ` sub-heading nested inside `## Criterion observations` does not
    # close the section — a deliberate, documented choice pinned here so a
    # future edit cannot silently invert it.
    result = _run(OBSERVATION_UNDER_SUBHEADING)
    assert result.returncode == 0, result.stderr
    assert "AC9" in result.stdout


# ---- item 11: manual-check attestation requirement ---------------------------


def test_manual_check_with_no_attestation_line_exits_1_naming_criterion():
    result = _run(MANUAL_CHECK_NO_ATTESTATION)
    assert result.returncode == 1
    assert "AC3" in result.stderr


def test_manual_check_with_attestation_for_a_different_criterion_exits_1():
    result = _run(MANUAL_CHECK_WRONG_ATTESTATION)
    assert result.returncode == 1
    assert "AC3" in result.stderr


def test_manual_check_with_matching_attestation_exits_0():
    result = _run(MANUAL_CHECK_WITH_ATTESTATION)
    assert result.returncode == 0, result.stderr


def test_automated_assertion_and_design_doc_review_unaffected_by_missing_attestations():
    # ALL_OBSERVED carries automated-assertion (AC9) and design-doc-review (AC7)
    # observations with no attestation lines for either — only AC3's manual-check
    # has one — and the whole parent still certifies clean.
    result = _run(ALL_OBSERVED)
    assert result.returncode == 0, result.stderr


# ---- item 12: duplicate Covers identifier -------------------------------------


def test_duplicate_covers_identifier_exits_1_naming_it():
    result = _run(DUPLICATE_COVERS)
    assert result.returncode == 1
    assert "AC9" in result.stderr


# ---- item 13: live-shape fixture -----------------------------------------------


def test_live_shape_fixture_parses_and_exits_1_naming_both_identifiers():
    result = _run(LIVE_PARTIAL_COVERAGE)
    assert result.returncode == 1
    assert "AC5" in result.stderr
    assert "AC6" in result.stderr


# ---- item 14: duplicate Covers field must fail closed --------------------------


def test_duplicate_unmasked_covers_field_exits_2_with_reason_code():
    # A first-match-wins scan would certify against the first, smaller
    # `**Covers:**` line and never check the second, larger claim.
    result = _run(DUPLICATE_COVERS_FIELD)
    assert result.returncode == 2
    assert _DUPLICATE_COVERS_FIELD_REASON_CODE in result.stderr


def test_covers_field_inside_fence_is_invisible_to_duplicate_check():
    result = _run(COVERS_FIELD_IN_FENCE)
    assert result.returncode == 0, result.stderr
    assert "AC1" in result.stdout


def test_covers_field_inside_html_comment_is_invisible_to_duplicate_check():
    result = _run(COVERS_FIELD_IN_HTML_COMMENT)
    assert result.returncode == 0, result.stderr
    assert "AC1" in result.stdout


# ---- item 15: empty/whitespace-only attestation guard ---------------------------


def test_manual_check_with_whitespace_only_attestation_exits_1_naming_criterion():
    result = _run(MANUAL_CHECK_WHITESPACE_ATTESTATION)
    assert result.returncode == 1
    assert "AC3" in result.stderr


# ---- item 16: duplicate attestation names the attestations section ------------


def test_duplicate_attestation_for_one_identifier_names_attestations_section():
    result = _run(DUPLICATE_ATTESTATION)
    assert result.returncode == 1
    assert "AC3" in result.stderr
    assert "Operator attestations" in result.stderr
    assert "duplicate observation for identifier" not in result.stderr


# ---- item 17: evidence text is inert -------------------------------------------


def test_evidence_shaped_like_command_substitution_and_traversal_is_inert():
    # The command-substitution half is pinned directly: a `subprocess` mutation
    # would create the marker file, and its absence catches that.
    #
    # The path half's evidence names a file that does not exist
    # (`../../nonexistent-path-for-observation-gate-inertness-check-9f31`) —
    # deliberately, so an unguarded `open(evidence)` or `Path(evidence).open()`
    # raises `FileNotFoundError` and the process crashes (non-zero exit),
    # which `returncode == 0` then catches. This does NOT make every path-as-
    # file access falsifiable: a bare `Path(evidence).exists()` whose result is
    # then discarded with no other effect crashes nothing, changes no output,
    # and is unfalsifiable by any test — that half of the claim is narrowed
    # out rather than asserted here.
    marker = Path("/tmp/observation_gate_inertness_marker_test")
    if marker.exists():
        marker.unlink()
    try:
        result = _run(INERT_EVIDENCE)
        assert result.returncode == 0, result.stderr
        assert not marker.exists(), (
            "evidence text must never be executed as a shell command"
        )
    finally:
        if marker.exists():
            marker.unlink()


# ---- item 18: **Partially covers:** is never read ------------------------------


def test_partially_covers_field_alone_is_never_read_and_does_not_refuse():
    result = _run(PARTIALLY_COVERS_ONLY)
    assert result.returncode == 0, result.stderr
    assert "covers: none" in result.stdout


# ---- item 19: unterminated masker fails closed ---------------------------------


def test_unterminated_fence_exits_2_with_reason_code():
    # An unclosed ``` fence masks the real **Covers:** line below it to end of
    # document — the gate must fail closed rather than certify `covers: none`
    # on a claim it never actually saw.
    result = _run(UNTERMINATED_FENCE)
    assert result.returncode == 2
    assert "reason:" in result.stderr
    assert _UNTERMINATED_MASKED_REGION_REASON_CODE in result.stderr
    assert "covers: none" not in result.stdout


def test_unterminated_html_comment_exits_2_with_reason_code():
    result = _run(UNTERMINATED_HTML_COMMENT)
    assert result.returncode == 2
    assert "reason:" in result.stderr
    assert _UNTERMINATED_MASKED_REGION_REASON_CODE in result.stderr
    assert "covers: none" not in result.stdout


# ---- item 20: invisible Unicode does not satisfy the emptiness bar --------------


def test_invisible_unicode_evidence_exits_1_naming_identifier():
    # A single U+200B ZERO WIDTH SPACE renders as nothing in any editor or
    # terminal, but `str.isspace()` is False for it — so `str.strip()` alone
    # treats it as non-empty evidence. The gate must not certify on that.
    result = _run(INVISIBLE_UNICODE_EVIDENCE)
    assert result.returncode == 1, result.stdout
    assert "AC9" in result.stderr


def test_invisible_unicode_attestation_exits_1_naming_criterion():
    # Same defect on the attestation side: a manual-check discharge must not
    # be satisfiable by a zero-width character standing in for the operator's
    # verbatim answer.
    result = _run(INVISIBLE_UNICODE_ATTESTATION)
    assert result.returncode == 1, result.stdout
    assert "AC3" in result.stderr


# ---- item 21: identifier patterns are anchored to ASCII digits -----------------


def test_fullwidth_digit_identifier_is_not_recognized_as_an_observation():
    # `**Covers:**` is parsed by the imported, unmodified `_COVERS_RE`, which
    # is Unicode-`\d`-aware and accepts the fullwidth digit U+FF19 in
    # `AC９` — this gate cannot change that shared pattern. But this gate's
    # own observation-line pattern must anchor to ASCII digits, so a bullet
    # written with the same fullwidth identifier never counts as a
    # well-formed observation for it — leaving the covered identifier
    # unobserved, and the gate refuses rather than certifying a non-ASCII
    # identifier that only visually resembles AC9.
    result = _run(FULLWIDTH_IDENTIFIER)
    assert result.returncode == 1, result.stdout
    assert "AC９" in result.stderr
