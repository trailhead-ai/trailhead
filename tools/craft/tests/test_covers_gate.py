"""Tests for the covers gate.

The gate certifies a drafted `--covers` identifier list against a spec's
declared `## Acceptance Criteria` before a slice parent record is written.
The spec body arrives on stdin; the drafted list is a `--covers` argument.

Fixtures are synthetic spec bodies under `tests/fixtures/` — never real vault
records.

Exit-code contract (matches `leak_gate.py`):
  0 → clean (every covered identifier is a real criterion, grammar valid)
  1 → violation (unknown identifier, bad grammar, duplicate — prints a
      `reason:` line)
  2 → error / fail-closed (empty or non-UTF-8 stdin, no `## Acceptance
      Criteria` heading, a spec declaring zero criterion identifiers under
      that heading — its own distinct `reason-code:` line — or a missing
      `--covers` argument)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
import pytest


REPO_ROOT = Path(__file__).parent.parent
GATE = REPO_ROOT / "plugins" / "craft" / "scripts" / "covers_gate.py"
FIXTURES = Path(__file__).parent / "fixtures"

NINE_CRITERIA_SPEC = (FIXTURES / "spec_ac1_to_ac9.md").read_text(encoding="utf-8")
MISSING_HEADING_SPEC = (FIXTURES / "spec_missing_ac_heading.md").read_text(encoding="utf-8")
INDENTED_HEADING_IN_FENCE_SPEC = (
    FIXTURES / "spec_indented_heading_in_fence.md"
).read_text(encoding="utf-8")
ZERO_CRITERIA_SPEC = (FIXTURES / "spec_zero_criteria.md").read_text(encoding="utf-8")
LOWERCASE_HEADING_SPEC = (FIXTURES / "spec_heading_case_insensitive.md").read_text(encoding="utf-8")
FENCED_ABOVE_SPEC = (FIXTURES / "spec_fenced_example_above_section.md").read_text(encoding="utf-8")
FENCED_INSIDE_SPEC = (FIXTURES / "spec_fenced_criteria_inside_section.md").read_text(encoding="utf-8")
FORGED_HEADING_SPEC = (
    FIXTURES / "spec_forged_heading_via_line_separator.md"
).read_text(encoding="utf-8")
FORGED_EMPTY_SECTION_SPEC = (
    FIXTURES / "spec_forged_empty_section_via_line_separator.md"
).read_text(encoding="utf-8")
HTML_COMMENT_HIDES_FORGED_AC_HEADING_SPEC = (
    FIXTURES / "spec_html_comment_hides_forged_ac_heading.md"
).read_text(encoding="utf-8")
HTML_COMMENT_PRECEDES_REAL_AC_HEADING_SPEC = (
    FIXTURES / "spec_html_comment_precedes_real_ac_heading.md"
).read_text(encoding="utf-8")
UNCLOSED_HTML_COMMENT_SPEC = (
    FIXTURES / "spec_unclosed_html_comment_masks_to_eof.md"
).read_text(encoding="utf-8")
DUPLICATE_AC_HEADING_SPEC = (
    FIXTURES / "spec_duplicate_ac_and_slices_headings.md"
).read_text(encoding="utf-8")
COMPOSED_COMMENT_HIDES_DUPLICATE_AC_HEADING_SPEC = (
    FIXTURES / "spec_composed_comment_hides_duplicate_ac_heading.md"
).read_text(encoding="utf-8")

_ZERO_CRITERIA_REASON_CODE = "reason-code: zero-criterion-identifiers"
_DUPLICATE_AC_HEADING_REASON_CODE = "reason-code: duplicate-acceptance-criteria-heading"


def _run(
    covers: str | None,
    spec_body: str,
    partial_covers: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(GATE)]
    if covers is not None:
        cmd += ["--covers", covers]
    if partial_covers is not None:
        cmd += ["--partial-covers", partial_covers]
    return subprocess.run(cmd, input=spec_body, capture_output=True, text=True)


# ---- clean path --------------------------------------------------------


@pytest.mark.parametrize(
    ("covers", "expected"),
    [
        pytest.param("AC2", 0, id="known_identifier_against_nine_criterion_spec_exits_0"),
        pytest.param("AC2 also covers login", 1, id="covers_value_with_prose_alongside_identifier_exits_1"),
        pytest.param("this covers the login flow", 1, id="covers_value_with_free_text_and_no_identifier_exits_1"),
        pytest.param("AC2, AC2", 1, id="duplicate_identifier_exits_1"),
        pytest.param("", 1, id="empty_covers_value_exits_1"),
        pytest.param("AC1,AC2", 0, id="no_space_after_comma_parses_and_exits_0"),
        pytest.param("AC1, AC2", 0, id="single_space_after_comma_parses_and_exits_0"),
        pytest.param("AC1,AC2,", 1, id="trailing_comma_exits_1"),
        pytest.param(
            "AC1,AC2,AC3,AC4,AC5,AC6,AC7,AC8,AC9",
            0,
            id="parser_ignores_grouping_subheading_and_nested_bullet",
        ),
        pytest.param("AC10", 1, id="parser_rejects_a_nested_bullet_as_a_bare_criterion"),
    ],
)
def test_covers_value_exits_with_the_expected_code(covers, expected):
    r = _run(covers, NINE_CRITERIA_SPEC)
    assert r.returncode == expected, r.stderr + r.stdout


# ---- unknown identifier -------------------------------------------------


def test_unknown_identifier_exits_1_and_names_it():
    r = _run("AC12", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC12" in r.stderr


# ---- anti-heuristic grammar cases ---------------------------------------


def test_neither_covers_nor_partial_covers_given_exits_2():
    """At least one of `--covers` or `--partial-covers` is required; an
    invocation naming neither flag must fail closed rather than certifying an
    empty claim."""
    r = _run(None, NINE_CRITERIA_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout


# ---- whitespace grammar ---------------------------------------------------


# ---- stdin / spec fail-closed --------------------------------------------


def test_empty_stdin_exits_2_not_1_or_0():
    r = _run("AC2", "")
    assert r.returncode == 2, r.stderr + r.stdout


def test_missing_acceptance_criteria_section_exits_2():
    r = _run("AC2", MISSING_HEADING_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout


def test_inline_prose_mention_of_heading_does_not_satisfy_the_anchor():
    """`spec_missing_ac_heading.md` mentions `## Acceptance Criteria` inline,
    mid-paragraph — a loose containment check would wrongly treat that as the
    real heading. The gate must anchor on the heading at line start."""
    assert "## Acceptance Criteria" in MISSING_HEADING_SPEC
    r = _run("AC2", MISSING_HEADING_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout


# ---- spec parser: sub-headings and nested bullets ------------------------


# ---- heading anchor: line-start only, never a loosely-indented match -----


def test_indented_heading_inside_a_fenced_example_is_not_the_anchor():
    """The fixture's worked example contains an indented `## Acceptance
    Criteria` heading inside a fenced code block, above the real section. A
    parser that anchors on `line.strip()` would treat that fake heading as
    the section start and never reach the real AC1/AC2 below it. The real
    identifiers must still certify."""
    r = _run("AC1,AC2", INDENTED_HEADING_IN_FENCE_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


# ---- zero-identifier spec: legacy shape fails closed with its own reason --


def test_zero_criterion_identifiers_exits_2_with_a_distinct_reason():
    """A spec whose `## Acceptance Criteria` heading is present but which
    declares no `**ACn.**` identifiers (predates the convention) must fail
    closed at exit 2 — never exit 1's 'unknown criterion identifier(s)',
    which misdiagnoses a legacy spec as a drafting error — and its reason
    line must name that the spec declares no criterion identifiers, so a
    caller can distinguish this case from every other exit-2 reason."""
    r = _run("AC1", ZERO_CRITERIA_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout
    assert "no criterion identifiers" in r.stderr, r.stderr


# ---- non-UTF-8 stdin fails closed, never a traceback ----------------------


def test_non_utf8_stdin_exits_2_not_a_traceback():
    cmd = [sys.executable, str(GATE), "--covers", "AC1"]
    r = subprocess.run(cmd, input=b"\xff\xfe not valid utf-8", capture_output=True)
    assert r.returncode == 2, r.stderr + r.stdout
    assert b"Traceback" not in r.stderr


# ---- --covers grammar: no trailing newline admitted ------------------------


def test_covers_value_with_trailing_newline_fails_grammar_not_membership():
    """`_COVERS_RE`'s trailing `$` (without MULTILINE) matches before a
    trailing newline, so `--covers "AC1\\n"` was wrongly accepted by the
    grammar and only then refused as an unknown identifier — misnaming the
    cause. It must be refused by the grammar check itself."""
    r = _run("AC1\n", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "not a valid identifier list" in r.stderr, r.stderr


# ---- heading casing: case-insensitive, still anchored at line start ------


def test_lowercase_heading_casing_is_still_the_anchor():
    """A spec writing `## Acceptance criteria` (lowercase `c`) — the shape
    several specs in this vault actually use — must still be found as the
    anchor; the parser must not require exact-case match."""
    r = _run("AC1", LOWERCASE_HEADING_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


def test_lowercase_heading_still_rejects_an_unknown_identifier():
    r = _run("AC12", LOWERCASE_HEADING_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout


# ---- fence-blindness: a fenced example must never certify a fake identifier --


def test_fenced_example_above_the_real_section_does_not_certify_its_fake_identifier():
    """A flush-left (unindented) fenced worked example above the real
    section declares a fake AC1. A fence-blind parser takes the first
    line-start heading match — inside the fence — and would wrongly certify
    AC1. The gate must never exit 0 on a fabricated identifier."""
    r = _run("AC1", FENCED_ABOVE_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC1" in r.stderr


def test_fenced_example_above_the_real_section_still_certifies_the_real_identifier():
    r = _run("AC2", FENCED_ABOVE_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


def test_fenced_example_inside_the_real_section_does_not_contribute_its_fake_identifier():
    """The real `## Acceptance Criteria` section contains a nested fenced
    worked example whose `- **AC99.**` line must not be counted as a real
    criterion — the section scan terminates only on `## `, so a fence-blind
    scanner lets the fenced bullet ride through as though it were real."""
    r = _run("AC99", FENCED_INSIDE_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC99" in r.stderr


def test_fenced_example_inside_the_real_section_still_certifies_real_identifiers():
    r = _run("AC1,AC2", FENCED_INSIDE_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


# ---- zero-identifier legacy path: stable machine-readable reason code ----


def test_zero_criterion_identifiers_carries_the_stable_reason_code():
    """Step 9's legacy carve-out in slice/SKILL.md must key on a stable,
    greppable token rather than the prose reason line, so a reword of the
    message can never silently break the carve-out."""
    r = _run("AC1", ZERO_CRITERIA_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _ZERO_CRITERIA_REASON_CODE in r.stderr, r.stderr


def test_reason_code_absent_on_missing_heading_exit_2():
    """The reason code is unique to the zero-identifier path — it must not
    appear on any other exit-2 reason, or the carve-out could wrongly widen
    to swallow an unrelated fail-closed exit."""
    r = _run("AC2", MISSING_HEADING_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _ZERO_CRITERIA_REASON_CODE not in r.stderr, r.stderr


def test_reason_code_absent_on_empty_stdin_exit_2():
    r = _run("AC2", "")
    assert r.returncode == 2, r.stderr + r.stdout
    assert _ZERO_CRITERIA_REASON_CODE not in r.stderr, r.stderr


def test_reason_code_absent_on_non_utf8_stdin_exit_2():
    cmd = [sys.executable, str(GATE), "--covers", "AC1"]
    r = subprocess.run(cmd, input=b"\xff\xfe not valid utf-8", capture_output=True)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _ZERO_CRITERIA_REASON_CODE.encode() not in r.stderr, r.stderr


# ---- CommonMark line grammar: str.splitlines() over-splits ---------------
#
# `str.splitlines()` treats U+2028 LINE SEPARATOR, U+2029 PARAGRAPH
# SEPARATOR, NEL, \v, and \f as line breaks. CommonMark (and every renderer
# a human reads the spec in) does not — only \r\n, \r, and \n end a line.
# A heading or bullet hidden behind one of these characters inside what
# looks like one ordinary prose paragraph must NOT be treated as real
# structure by the parser.


def test_forged_heading_hidden_behind_line_separator_never_certifies_the_fabricated_id():
    """The fixture hides a fake `## Acceptance Criteria` heading and a fake
    `- **AC99.**` bullet behind U+2028 inside a prose paragraph, above the
    real section which declares only AC1. A parser that treats U+2028 as a
    line break anchors on the fake heading and wrongly certifies AC99."""
    r = _run("AC99", FORGED_HEADING_SPEC)
    assert r.returncode != 0, (
        "the gate must never certify AC99 — it exists only behind a hidden "
        f"U+2028 line-separator, not as real CommonMark structure: {r.stdout}"
    )


def test_forged_heading_hidden_behind_line_separator_still_certifies_the_real_id():
    r = _run("AC1", FORGED_HEADING_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


def test_forged_empty_section_hidden_behind_paragraph_separator_does_not_trigger_legacy_carveout():
    """The fixture hides a fake, bullet-less `## Acceptance Criteria`
    heading behind U+2029 inside a prose paragraph, above the real section
    which declares AC1 and AC2. A parser that treats U+2029 as a line break
    anchors on the fake empty heading and wrongly emits the legacy
    zero-criterion-identifiers carve-out on a spec that really does declare
    criteria — silently and durably skipping coverage recording."""
    r = _run("AC1", FORGED_EMPTY_SECTION_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout
    assert _ZERO_CRITERIA_REASON_CODE not in r.stderr, r.stderr


def test_forged_empty_section_hidden_behind_paragraph_separator_certifies_both_real_ids():
    r = _run("AC1,AC2", FORGED_EMPTY_SECTION_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


def test_windows_line_endings_still_parse_correctly():
    r = _run("AC1,AC2", NINE_CRITERIA_SPEC.replace("\n", "\r\n"))
    assert r.returncode == 0, r.stderr + r.stdout


def test_bare_cr_line_endings_still_parse_correctly():
    r = _run("AC1,AC2", NINE_CRITERIA_SPEC.replace("\n", "\r"))
    assert r.returncode == 0, r.stderr + r.stdout


# ---- HTML comment blindness: a payload hidden in a comment must never certify --
#
# A CommonMark HTML comment (`<!-- ... -->`) is dropped entirely from every
# rendered view of the document, to end of document if never closed. A gate
# that is blind to that is trusting structure a human reviewer never sees.


def test_html_comment_hides_forged_ac_heading_and_never_certifies_it():
    """The fixture's only `## Acceptance Criteria` heading and only AC1
    criterion live inside an HTML comment. A comment-blind parser anchors on
    the forged heading and wrongly certifies AC1. The gate must fail closed
    with no real heading found."""
    r = _run("AC1", HTML_COMMENT_HIDES_FORGED_AC_HEADING_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout


def test_html_comment_does_not_shadow_the_real_ac_heading_that_follows():
    """A decoy `## Acceptance Criteria` heading lives inside a comment above
    the real section. The masked decoy must not be picked as the anchor —
    the real section's AC1/AC2 must still certify."""
    r = _run("AC1,AC2", HTML_COMMENT_PRECEDES_REAL_AC_HEADING_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


def test_unclosed_html_comment_masks_to_end_of_document():
    """An unclosed `<!--` masks everything through end of document, per
    CommonMark — the forged AC2 inside it must never become a real
    criterion, while the real AC1 declared before the comment opened still
    certifies."""
    r = _run("AC1", UNCLOSED_HTML_COMMENT_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout
    r = _run("AC2", UNCLOSED_HTML_COMMENT_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC2" in r.stderr


# ---- heading uniqueness: a second unmasked heading is a fail-closed error --


def test_duplicate_unmasked_ac_heading_fails_closed_with_its_own_reason_code():
    """A visible second `## Acceptance Criteria` heading silently replaces
    the real criteria set for a first-match-wins scanner. The gate must
    detect the second occurrence and fail closed rather than silently
    picking the first."""
    r = _run("AC1", DUPLICATE_AC_HEADING_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _DUPLICATE_AC_HEADING_REASON_CODE in r.stderr, r.stderr


def test_composed_html_comment_hides_its_own_internal_duplicate_heading():
    """The two findings chained: an HTML comment hides a decoy section that
    itself contains a duplicate `## Acceptance Criteria` heading. Both
    decoy occurrences are masked, so the single real heading outside the
    comment remains unique — the gate must certify normally, not report a
    false duplicate and not anchor on either decoy."""
    r = _run("AC1,AC2", COMPOSED_COMMENT_HIDES_DUPLICATE_AC_HEADING_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


# ---- --partial-covers: a drafted partial-coverage list certifies alongside --covers --


def test_partial_covers_naming_declared_identifiers_exits_0():
    r = _run("AC1", NINE_CRITERIA_SPEC, partial_covers="AC3")
    assert r.returncode == 0, r.stderr + r.stdout


def test_partial_covers_naming_undeclared_identifier_exits_1_and_names_it():
    r = _run(None, NINE_CRITERIA_SPEC, partial_covers="AC12")
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC12" in r.stderr


def test_partial_covers_violating_grammar_exits_1():
    r = _run(None, NINE_CRITERIA_SPEC, partial_covers="AC3 also partial")
    assert r.returncode == 1, r.stderr + r.stdout


def test_partial_covers_repeating_identifier_exits_1_with_no_covers_given():
    """The duplicate guard comes from the shared `parse_covers` — asserted at
    the `--partial-covers` call site specifically, with no `--covers` given,
    so a future refactor that stops sharing `parse_covers` between the two
    flags cannot silently drop the check on this path."""
    r = _run(None, NINE_CRITERIA_SPEC, partial_covers="AC3,AC3")
    assert r.returncode == 1, r.stderr + r.stdout
    assert "repeats identifier" in r.stderr, r.stderr


def test_partial_covers_alone_with_no_covers_exits_0():
    """The AC5 case — a slice delivers only part of a criterion and nothing
    else."""
    r = _run(None, NINE_CRITERIA_SPEC, partial_covers="AC5")
    assert r.returncode == 0, r.stderr + r.stdout


def test_covers_and_partial_covers_together_over_disjoint_sets_exits_0():
    r = _run("AC1,AC2", NINE_CRITERIA_SPEC, partial_covers="AC3,AC4")
    assert r.returncode == 0, r.stderr + r.stdout


def test_same_identifier_in_both_lists_exits_1_and_names_the_overlap():
    r = _run("AC1,AC2", NINE_CRITERIA_SPEC, partial_covers="AC2,AC3")
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC2" in r.stderr


# ---- parse_covers names the flag that was actually passed -----------------


def test_covers_grammar_violation_still_names_the_covers_flag():
    r = _run("not-an-id", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "--covers value" in r.stderr, r.stderr


def test_partial_covers_grammar_violation_names_the_partial_covers_flag_not_covers():
    """A grammar violation on the `--partial-covers` path must name
    `--partial-covers` in its `reason:` text, never the `--covers` flag the
    caller never passed."""
    r = _run(None, NINE_CRITERIA_SPEC, partial_covers="not-an-id")
    assert r.returncode == 1, r.stderr + r.stdout
    assert "--partial-covers value" in r.stderr, r.stderr
    assert "--covers value" not in r.stderr, r.stderr


# ---- unknown-identifier stderr names the source flag and reports both lists --


def test_unknown_identifiers_in_both_lists_are_named_by_flag_in_one_pass():
    """An invocation with an unknown identifier in each list must not stop
    after the first — both lists' unknowns are reported together, each
    named by the flag it came from, so a caller supplying both flags does
    not need a second round trip."""
    r = _run("AC12", NINE_CRITERIA_SPEC, partial_covers="AC13")
    assert r.returncode == 1, r.stderr + r.stdout
    assert "--covers" in r.stderr, r.stderr
    assert "--partial-covers" in r.stderr, r.stderr
    assert "AC12" in r.stderr, r.stderr
    assert "AC13" in r.stderr, r.stderr


# ---- the neither-flag exit-2 path carries its own reason-code -------------


def test_neither_covers_nor_partial_covers_given_carries_its_own_reason_code():
    r = _run(None, NINE_CRITERIA_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout
    assert "reason-code: no-coverage-list-given" in r.stderr, r.stderr


# ---- --folds: certify the follow-ups a slice folds -------------------------


def _entry(fid: str, status: str = "open") -> str:
    return (
        f"- **{fid}.** Title {fid}\n"
        f"  - status: {status}\n"
        "  - what: do the thing\n"
        "  - where: `a/b.py`\n"
        "  - found: while building\n"
    )


FOLLOWUPS_SECTION = (
    "\n## Follow-ups\n\n"
    + _entry("F1")
    + _entry("F2", "folded into task/x")
    + _entry("F3")
    + _entry("F4", "done by task/y")
    + _entry("F5", "dropped: not worth it")
)
WITH_FOLLOWUPS_SPEC = NINE_CRITERIA_SPEC + FOLLOWUPS_SECTION
ZERO_CRITERIA_WITH_FOLLOWUPS_SPEC = ZERO_CRITERIA_SPEC + FOLLOWUPS_SECTION


def _run_folds(
    folds: str | None,
    spec_body: str,
    covers: str | None = None,
    partial_covers: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(GATE)]
    if folds is not None:
        cmd += ["--folds", folds]
    if covers is not None:
        cmd += ["--covers", covers]
    if partial_covers is not None:
        cmd += ["--partial-covers", partial_covers]
    return subprocess.run(cmd, input=spec_body, capture_output=True, text=True, env=env)


def test_folds_alone_over_open_entries_exits_0_on_a_spec_with_criteria():
    r = _run_folds("F1, F3", WITH_FOLLOWUPS_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


def test_folds_alone_exits_0_on_a_spec_with_zero_criterion_identifiers():
    r = _run_folds("F1, F3", ZERO_CRITERIA_WITH_FOLLOWUPS_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


def test_folds_alone_exits_0_on_a_spec_with_no_acceptance_criteria_heading():
    r = _run_folds("F1", MISSING_HEADING_SPEC + FOLLOWUPS_SECTION)
    assert r.returncode == 0, r.stderr + r.stdout


def test_covers_with_folds_both_certified_exits_0():
    r = _run_folds("F1", WITH_FOLLOWUPS_SPEC, covers="AC2")
    assert r.returncode == 0, r.stderr + r.stdout


def test_unknown_covers_with_valid_folds_exits_1_naming_the_criterion():
    r = _run_folds("F1", WITH_FOLLOWUPS_SPEC, covers="AC99")
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC99" in r.stderr


def test_partial_covers_with_folds_still_runs_the_criteria_path():
    r = _run_folds("F1", WITH_FOLLOWUPS_SPEC, partial_covers="AC99")
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC99" in r.stderr


def test_covers_with_folds_on_zero_criterion_spec_keeps_the_criteria_exit_2():
    r = _run_folds("F1", ZERO_CRITERIA_WITH_FOLLOWUPS_SPEC, covers="AC1")
    assert r.returncode == 2, r.stderr + r.stdout
    assert _ZERO_CRITERIA_REASON_CODE in r.stderr


@pytest.mark.parametrize("fid", ["F2", "F4", "F5", "F9"])
def test_folds_naming_a_non_open_or_absent_entry_exits_1_naming_it(fid):
    r = _run_folds(fid, WITH_FOLLOWUPS_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout
    assert fid in r.stderr


def test_folds_with_one_open_and_one_non_open_names_only_the_bad_one():
    r = _run_folds("F1, F2", WITH_FOLLOWUPS_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "F2" in r.stderr
    assert "F1" not in r.stderr


@pytest.mark.parametrize("value", ["F1, F1", "AC1", "", "F0", "F1,F3 ", "F1; F3"])
def test_folds_with_bad_shape_exits_1(value):
    r = _run_folds(value, WITH_FOLLOWUPS_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout


def test_folds_repeat_names_the_repeated_id():
    r = _run_folds("F1, F1", WITH_FOLLOWUPS_SPEC)
    assert "F1" in r.stderr


def test_folds_without_comma_space_is_accepted():
    r = _run_folds("F1,F3", WITH_FOLLOWUPS_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


def test_folds_on_a_spec_with_no_followups_section_exits_1_naming_the_id():
    r = _run_folds("F1", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "F1" in r.stderr


def test_folds_over_a_malformed_followups_section_exits_2():
    spec = NINE_CRITERIA_SPEC + "\n## Follow-ups\n\n- **F1.** Title\n  - status: bogus\n"
    r = _run_folds("F1", spec)
    assert r.returncode == 2, r.stderr + r.stdout
    assert "reason-code: " in r.stderr


def test_folds_certifies_when_the_interpreter_does_not_put_the_script_directory_on_sys_path():
    env = {**os.environ, "PYTHONSAFEPATH": "1"}
    r = _run_folds("F1, F3", WITH_FOLLOWUPS_SPEC, env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "ModuleNotFoundError" not in r.stderr


def test_folds_refuses_a_duplicated_followups_heading_with_exit_2_when_the_script_directory_is_not_on_sys_path():
    spec = WITH_FOLLOWUPS_SPEC + "\n## Follow-ups\n\n- **F9.** Another\n"
    r = _run_folds("F1", spec, env={**os.environ, "PYTHONSAFEPATH": "1"})
    assert r.returncode == 2, r.stderr + r.stdout
    assert "reason-code: duplicate-followups-heading" in r.stderr


@pytest.mark.parametrize("covers", [None, "AC1"])
def test_folds_over_a_duplicated_followups_heading_exits_2_with_the_reason_code(covers):
    spec = WITH_FOLLOWUPS_SPEC + "\n## Follow-ups\n\n- **F9.** Another\n"
    r = _run_folds("F1", spec, covers=covers)
    assert r.returncode == 2, r.stderr + r.stdout
    assert "reason-code: duplicate-followups-heading" in r.stderr
    assert "Traceback" not in r.stderr


def test_folds_refusal_reports_the_reason_code_without_echoing_vault_text():
    spec = (
        NINE_CRITERIA_SPEC
        + "\n## Follow-ups\n\n- **F1** zebra-quokka-marker no period after the id\n"
    )
    r = _run_folds("F1", spec)
    assert r.returncode == 2, r.stderr + r.stdout
    assert "reason-code: " in r.stderr
    assert "zebra-quokka-marker" not in r.stderr


def test_no_flag_at_all_still_exits_2_no_coverage_list_given():
    r = _run_folds(None, WITH_FOLLOWUPS_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout
    assert "reason-code: no-coverage-list-given" in r.stderr


@pytest.mark.parametrize("order", [("covers_gate", "followups"), ("followups", "covers_gate")])
def test_covers_gate_and_followups_import_in_either_order(order):
    scripts = str(GATE.parent)
    code = f"import sys; sys.path.insert(0, {scripts!r}); import {order[0]}; import {order[1]}"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_covers_with_a_non_open_fold_exits_1_naming_the_fold():
    r = _run_folds("F2", WITH_FOLLOWUPS_SPEC, covers="AC2")
    assert r.returncode == 1, r.stderr + r.stdout
    assert "F2" in r.stderr


@pytest.mark.parametrize("value", ["F0", "AC1", "", "F1,F3 ", "F1; F3"])
def test_folds_shape_violation_is_reported_as_a_shape_error_not_an_unknown_id(value):
    r = _run_folds(value, WITH_FOLLOWUPS_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "not a valid follow-up id list" in r.stderr
