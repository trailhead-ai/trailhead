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
  2 → could not certify: empty/non-UTF-8 stdin, a malformed `**Covers:**`
      value (`reason-code: malformed-covers-field`), or a second unmasked
      `## Criterion observations` heading
      (`reason-code: duplicate-observations-section`)
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

_MALFORMED_COVERS_REASON_CODE = "reason-code: malformed-covers-field"
_DUPLICATE_OBSERVATIONS_SECTION_REASON_CODE = "reason-code: duplicate-observations-section"


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


def test_empty_stdin_exits_2():
    result = _run("")
    assert result.returncode == 2


def test_non_utf8_stdin_exits_2():
    result = _run(b"\xff\xfe\x00\x01 not utf-8")
    assert result.returncode == 2


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
