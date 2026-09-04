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

import subprocess
import sys
from pathlib import Path

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


def _run(covers: str | None, spec_body: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(GATE)]
    if covers is not None:
        cmd += ["--covers", covers]
    return subprocess.run(cmd, input=spec_body, capture_output=True, text=True)


# ---- clean path --------------------------------------------------------


def test_known_identifier_against_nine_criterion_spec_exits_0():
    r = _run("AC2", NINE_CRITERIA_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


# ---- unknown identifier -------------------------------------------------


def test_unknown_identifier_exits_1_and_names_it():
    r = _run("AC12", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC12" in r.stderr


# ---- anti-heuristic grammar cases ---------------------------------------


def test_covers_value_with_prose_alongside_identifier_exits_1():
    r = _run("AC2 also covers login", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout


def test_covers_value_with_free_text_and_no_identifier_exits_1():
    r = _run("this covers the login flow", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout


def test_duplicate_identifier_exits_1():
    r = _run("AC2, AC2", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout


def test_empty_covers_value_exits_1():
    r = _run("", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout


def test_missing_covers_argument_exits_2():
    r = _run(None, NINE_CRITERIA_SPEC)
    assert r.returncode == 2, r.stderr + r.stdout


# ---- whitespace grammar ---------------------------------------------------


def test_no_space_after_comma_parses_and_exits_0():
    r = _run("AC1,AC2", NINE_CRITERIA_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


def test_single_space_after_comma_parses_and_exits_0():
    r = _run("AC1, AC2", NINE_CRITERIA_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


def test_trailing_comma_exits_1():
    r = _run("AC1,AC2,", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout


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


def test_parser_ignores_grouping_subheading_and_nested_bullet():
    """The fixture spec has a `###` grouping sub-heading and a nested
    sub-bullet under AC4 — neither should be countable as a criterion, so a
    `--covers` value naming only real top-level identifiers (AC1-AC9) must
    still exit 0, proving the parser didn't choke on or miscount them."""
    r = _run("AC1,AC2,AC3,AC4,AC5,AC6,AC7,AC8,AC9", NINE_CRITERIA_SPEC)
    assert r.returncode == 0, r.stderr + r.stdout


def test_parser_rejects_a_nested_bullet_as_a_bare_criterion():
    """The nested sub-bullet under AC4 forms no criterion of its own, so no
    identifier for it exists — asserting an out-of-range identifier like
    AC10 (there is no tenth criterion) exits 1."""
    r = _run("AC10", NINE_CRITERIA_SPEC)
    assert r.returncode == 1, r.stderr + r.stdout


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
    assert "unknown criterion identifier" not in r.stderr, r.stderr


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
