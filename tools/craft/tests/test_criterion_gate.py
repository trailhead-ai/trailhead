"""Tests for the criterion content gate.

The gate reads a spec body on stdin and enforces two bars over every
criterion under `## Acceptance Criteria`: no implementation identifier
(AC3), and exactly one sanctioned verification method (AC4). It reuses
`covers_gate.parse_criteria_with_text` (the sibling accessor to
`parse_criteria`) for the walk, and `observation_gate._SANCTIONED_METHODS`
for the accepted method vocabulary — never re-deriving either.

Fixtures are synthetic spec bodies under `tests/fixtures/` — except
`crit_live_spec_ac1_to_ac9.md` (captured verbatim from
`spec/acceptance-criteria-are-atomic-assertions-a-slice-carries`) and
`crit_live_legacy_draft.md` (captured verbatim from a real draft spec that
takes the legacy carve-out).

Exit-code contract (matches the sibling gates):
  0 → certified — every criterion is clean, stdout names each identifier
      with its declared method
  1 → integrity violation — a refused code-location span, a missing/bad
      verification trailer, or an unidentified bullet in a section that
      also declares identifiers (prints a `reason:` line)
  2 → could not certify — fail-closed: empty/non-UTF-8 stdin, no
      `## Acceptance Criteria` heading, a spec declaring zero criterion
      identifiers (`reason-code: zero-criterion-identifiers`), a second
      unmasked heading (`reason-code: duplicate-acceptance-criteria-heading`),
      or the document ending inside an open fence/comment
      (`reason-code: unterminated-masked-region`)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
GATE = REPO_ROOT / "plugins" / "craft" / "scripts" / "criterion_gate.py"
FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _run(spec_body: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE)], input=spec_body, capture_output=True, text=True
    )


ALL_CLEAN = _fixture("crit_all_clean.md")
ZERO_IDENTIFIERS = _fixture("crit_zero_identifiers.md")
REFUSED_CODE_LOCATION = _fixture("crit_refused_code_location.md")
ALLOW_FORMS = _fixture("crit_allow_forms.md")
REFUSE_FORMS = _fixture("crit_refuse_forms.md")
NO_TRAILER = _fixture("crit_no_trailer.md")
UNSANCTIONED_TOKEN = _fixture("crit_unsanctioned_token.md")
TWO_METHODS = _fixture("crit_two_methods.md")
AC_HEADING_IN_FENCE = _fixture("crit_ac_heading_in_fence.md")
CRITERION_IN_FENCE = _fixture("crit_criterion_in_fence.md")
CRITERION_IN_HTML_COMMENT = _fixture("crit_criterion_in_html_comment.md")
DUPLICATE_HEADING = _fixture("crit_duplicate_heading.md")
UNTERMINATED_FENCE = _fixture("crit_unterminated_fence.md")
UNTERMINATED_HTML_COMMENT = _fixture("crit_unterminated_html_comment.md")
INVISIBLE_TRAILER = _fixture("crit_invisible_trailer.md")
ACCENTED_PROSE = _fixture("crit_accented_prose.md")
LIVE_SPEC_AC1_TO_AC9 = _fixture("crit_live_spec_ac1_to_ac9.md")
LIVE_LEGACY_DRAFT = _fixture("crit_live_legacy_draft.md")
MULTIPLE_VIOLATIONS = _fixture("crit_multiple_violations.md")
MIXED_IDENTIFIERS = _fixture("crit_mixed_identifiers.md")
CREDENTIAL_SPAN = _fixture("crit_credential_span.md")
CAMELCASE_BOUNDARY = _fixture("crit_camelcase_boundary.md")

_ZERO_CRITERIA_REASON_CODE = "reason-code: zero-criterion-identifiers"
_DUPLICATE_HEADING_REASON_CODE = "reason-code: duplicate-acceptance-criteria-heading"
_UNTERMINATED_REGION_REASON_CODE = "reason-code: unterminated-masked-region"

_SANCTIONED_METHODS = {"automated-assertion", "design-doc-review", "manual-check"}


# ---- item 1: clean spec certifies, stdout names identifier + method ------


def test_all_clean_spec_exits_0_and_names_each_identifier_with_its_method():
    r = _run(ALL_CLEAN)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "AC1" in r.stdout and "automated-assertion" in r.stdout
    assert "AC2" in r.stdout and "design-doc-review" in r.stdout
    assert "AC3" in r.stdout and "manual-check" in r.stdout


# ---- item 2: zero identifiers takes the legacy carve-out ------------------


def test_zero_criterion_identifiers_exits_2_with_carveout_reason_code():
    r = _run(ZERO_IDENTIFIERS)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _ZERO_CRITERIA_REASON_CODE in r.stderr


# ---- item 3: refused span names both criterion and span -------------------


def test_refused_code_location_span_exits_1_naming_criterion_and_span():
    r = _run(REFUSED_CODE_LOCATION)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC2" in r.stderr
    assert "event_id" in r.stderr


# ---- item 4: each allowed product-surface form certifies ------------------


def test_each_allowed_product_surface_form_certifies():
    r = _run(ALLOW_FORMS)
    assert r.returncode == 0, r.stderr + r.stdout
    for identifier in ("AC1", "AC2", "AC3", "AC4", "AC5"):
        assert identifier in r.stdout


# ---- item 5: each refused code-location form refuses ----------------------


def test_each_refused_code_location_form_refuses():
    r = _run(REFUSE_FORMS)
    assert r.returncode == 1, r.stderr + r.stdout
    for identifier in ("AC1", "AC2", "AC3", "AC4", "AC5"):
        assert identifier in r.stderr, f"{identifier} missing from: {r.stderr}"


# ---- item 6: no verification trailer ---------------------------------------


def test_no_verification_trailer_exits_1_naming_identifier():
    r = _run(NO_TRAILER)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC2" in r.stderr


# ---- item 7: unsanctioned method token -------------------------------------


def test_unsanctioned_method_token_exits_1_naming_token_and_identifier():
    r = _run(UNSANCTIONED_TOKEN)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC2" in r.stderr
    assert "manual-testing" in r.stderr


def test_unsanctioned_method_refusal_lists_all_three_sanctioned_tokens():
    """Item 16: the refusal names the remedy, not just the fault."""
    r = _run(UNSANCTIONED_TOKEN)
    assert r.returncode == 1, r.stderr + r.stdout
    for method in _SANCTIONED_METHODS:
        assert method in r.stderr, f"{method} missing from remedy text: {r.stderr}"


# ---- item 8: exactly one method ---------------------------------------------


def test_trailer_naming_two_methods_exits_1():
    r = _run(TWO_METHODS)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC2" in r.stderr


# ---- item 9: spaced and hyphenated spellings both certify (executed) -------


def _spec_with_trailer(method_text: str) -> str:
    return (
        "## Acceptance Criteria\n\n"
        f"- **AC1.** A clean criterion. *Verified by: {method_text}.*\n\n"
        "## Non-Goals\n\nn/a — fixture only.\n"
    )


def test_spaced_and_hyphenated_spellings_of_sanctioned_methods_certify():
    for spaced, hyphenated in (
        ("automated assertion", "automated-assertion"),
        ("design doc review", "design-doc-review"),
        ("manual check", "manual-check"),
    ):
        r_spaced = _run(_spec_with_trailer(spaced))
        assert r_spaced.returncode == 0, f"{spaced!r}: {r_spaced.stderr}"
        assert hyphenated in r_spaced.stdout

        r_hyphenated = _run(_spec_with_trailer(hyphenated))
        assert r_hyphenated.returncode == 0, f"{hyphenated!r}: {r_hyphenated.stderr}"
        assert hyphenated in r_hyphenated.stdout


def test_accepted_method_set_is_derived_by_executing_the_gate():
    """Never import the gate's own constant and compare it to itself — run
    every candidate token through the gate and observe the exit code, the
    discipline `test_verification_method_vocabulary.py` establishes."""
    candidates = _SANCTIONED_METHODS | {"manual-testing", "peer-review", "code-review"}
    accepted = set()
    for token in candidates:
        r = _run(_spec_with_trailer(token))
        if r.returncode == 0:
            accepted.add(token)
        else:
            assert r.returncode == 1, f"{token!r} produced {r.returncode}: {r.stderr}"
    assert accepted == _SANCTIONED_METHODS


# ---- item 10: empty / non-UTF-8 stdin never exit 0 -------------------------


def test_empty_stdin_exits_2():
    r = _run("")
    assert r.returncode == 2, r.stderr + r.stdout
    assert "reason-code: empty-stdin" in r.stderr


def test_non_utf8_stdin_exits_2():
    r = subprocess.run(
        [sys.executable, str(GATE)], input=b"\xff\xfe\x00garbage", capture_output=True
    )
    assert r.returncode == 2, r.stderr + r.stdout
    assert b"reason-code: non-utf8-stdin" in r.stderr


# ---- item 11: forged-structure class ---------------------------------------


def test_ac_heading_inside_fence_is_invisible_to_heading_search():
    r = _run(AC_HEADING_IN_FENCE)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _ZERO_CRITERIA_REASON_CODE not in r.stderr


def test_criterion_bullet_inside_fence_is_invisible_to_scan():
    r = _run(CRITERION_IN_FENCE)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "AC99" not in r.stdout


def test_criterion_bullet_inside_html_comment_is_invisible_to_scan():
    r = _run(CRITERION_IN_HTML_COMMENT)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "AC99" not in r.stdout


def test_duplicate_ac_heading_exits_2_with_inherited_reason_code():
    r = _run(DUPLICATE_HEADING)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _DUPLICATE_HEADING_REASON_CODE in r.stderr


# ---- item 12: unterminated-region class ------------------------------------


def test_unterminated_fence_exits_2_never_0():
    r = _run(UNTERMINATED_FENCE)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _UNTERMINATED_REGION_REASON_CODE in r.stderr


def test_unterminated_html_comment_exits_2_never_0():
    r = _run(UNTERMINATED_HTML_COMMENT)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _UNTERMINATED_REGION_REASON_CODE in r.stderr


# ---- item 13: invisible-content class --------------------------------------


def test_trailer_of_only_invisible_unicode_does_not_satisfy_names_a_method():
    r = _run(INVISIBLE_TRAILER)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC2" in r.stderr


def test_accented_non_ascii_prose_still_certifies():
    r = _run(ACCENTED_PROSE)
    assert r.returncode == 0, r.stderr + r.stdout


# ---- item 14: live-shape fixtures -------------------------------------------


def test_live_spec_nine_criteria_certify():
    r = _run(LIVE_SPEC_AC1_TO_AC9)
    assert r.returncode == 0, r.stderr + r.stdout
    for n in range(1, 10):
        assert f"AC{n}" in r.stdout


def test_live_legacy_draft_spec_takes_the_carveout():
    r = _run(LIVE_LEGACY_DRAFT)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _ZERO_CRITERIA_REASON_CODE in r.stderr


# ---- item 15: every failing criterion named in one pass ---------------------


def test_three_distinct_violations_named_in_a_single_refusal():
    r = _run(MULTIPLE_VIOLATIONS)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC2" in r.stderr
    assert "AC3" in r.stderr
    assert "AC4" in r.stderr


# ---- item 17: mixed-identifier sections -------------------------------------


def test_mixed_identifier_section_gates_identified_and_refuses_unidentified():
    r = _run(MIXED_IDENTIFIERS)
    assert r.returncode == 1, r.stderr + r.stdout
    # the identified bullets are clean; only the unidentified bullet refuses
    assert "AC1" not in r.stderr
    assert "AC2" not in r.stderr


# ---- item 18: credential-scrubbed refusal output -----------------------------


def test_credential_shaped_span_refuses_without_reproducing_the_secret():
    r = _run(CREDENTIAL_SPAN)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "sk_live_Zq7Kd2" not in r.stderr
    assert "AC1" in r.stderr


# ---- CamelCase boundary (both directions), per U1's binding correction ------


def test_camelcase_genuine_alternation_refuses_and_allcaps_certifies():
    r = _run(CAMELCASE_BOUNDARY)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC1" in r.stderr
    assert "AC2" not in r.stderr
