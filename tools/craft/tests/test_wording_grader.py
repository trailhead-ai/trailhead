"""Tests for the wording grader.

The grader reads one captured model output on stdin and prints one verdict line
for the check named by `--check`. Its grammar is fixed in the ritual-output-wording
eval case's `expected.md`; these tests drive the script as a subprocess, varying the
input across each rule that decides a verdict.

Exit codes: 0 on a passing verdict, 1 on any other verdict, 2 when it could not
grade (with a `reason-code:` line on stderr and nothing on stdout).
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

CRAFT_ROOT = Path(__file__).parent.parent
GRADER = CRAFT_ROOT / "plugins" / "craft" / "scripts" / "wording_grader.py"
CASE = CRAFT_ROOT / "plugins" / "craft" / "evals" / "ritual-output-wording"
SPEC = CASE / "fixtures" / "spec.md"
RUNS = CASE / "runs"
EXPECTED_MD = CASE / "expected.md"

PASSING = {"text-with-number", "template-shape", "gate-shape"}

AC4 = "A librarian override records who granted it and the reason given, and shows in the borrower's history"

SMALL_CRITERIA = (
    "**AC1.** alpha beta gamma delta\n\n"
    "**AC2.** gamma delta epsilon zeta\n\n"
    "**AC3.** kappa omega sigma theta\n"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("wording_grader_under_test", GRADER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_grader(args, stdin=b"", timeout=60):
    return subprocess.run(
        [sys.executable, str(GRADER), *args],
        input=stdin,
        capture_output=True,
        timeout=timeout,
    )


def grade(check, text, criteria=SPEC, extra=()):
    """Run the grader and return the verdict word; the exit code must agree with it."""
    args = ["--check", check, *extra]
    if criteria is not None:
        args += ["--criteria", str(criteria)]
    result = run_grader(args, text.encode("utf-8"))
    stdout = result.stdout.decode("utf-8")
    stderr = result.stderr.decode("utf-8")
    match = re.fullmatch(rf"{re.escape(check)}: ([a-z-]+)\n", stdout)
    assert match, f"expected one verdict line, got stdout={stdout!r} stderr={stderr!r}"
    word = match.group(1)
    assert result.returncode == (0 if word in PASSING else 1), (word, result.returncode, stderr)
    return word


def mention(text, **kw):
    return grade("criterion-mention", text, **kw)


@pytest.fixture
def small(tmp_path):
    path = tmp_path / "criteria.md"
    path.write_text(SMALL_CRITERIA, encoding="utf-8")
    return path


def could_not_grade(args, stdin, reason, timeout=60):
    result = run_grader(args, stdin, timeout=timeout)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert result.stdout == b""
    assert f"reason-code: {reason}\n" in result.stderr.decode("utf-8")


# --- criterion-mention: bare / text-with-number / no-mention ---------------------------------


def test_same_criterion_bare_or_in_trailing_brackets_after_its_text():
    assert mention(f"AC4 {AC4}.") == "bare"
    assert mention(f"{AC4} (AC4).") == "text-with-number"


def test_one_bare_mention_among_several_makes_the_capture_bare():
    both_bracketed = f"{AC4} (AC4).\n\nA borrower owing more than ten pounds in late fees cannot check out (AC3)."
    assert mention(both_bracketed) == "text-with-number"
    assert mention(both_bracketed + "\n\nAC5 is open.") == "bare"


def test_no_criterion_number_is_no_mention_with_a_nonzero_exit():
    result = run_grader(["--check", "criterion-mention", "--criteria", str(SPEC)], b"Nothing is named here.\n")
    assert result.stdout == b"criterion-mention: no-mention\n"
    assert result.returncode == 1


def test_a_bracketed_number_with_no_words_before_it_is_bare():
    assert mention("(AC4) Override recording is not started") == "bare"
    assert mention("Some text.\n\n(AC4)") == "bare"
    assert mention("Some text.\n- (AC4)") == "bare"


def test_words_before_the_brackets_decide_text_or_nickname():
    assert mention(f"{AC4} (AC4)") == "text-with-number"
    assert mention("Override recording is not started (AC4)") == "nickname"


@pytest.mark.parametrize(
    "text, verdict",
    [
        pytest.param(f"{AC4} (AC4)", "text-with-number", id="own-text-then-number"),
        pytest.param("Override recording is not started (AC4)", "nickname", id="two-word-nickname"),
        pytest.param("**AC4** — the override records who granted it", "bare", id="leading-bold-number"),
        pytest.param("| AC4 | Override recording | not started |", "bare", id="table-row"),
        pytest.param("Still to do: AC4, AC5 and AC6", "bare", id="unbracketed-list"),
        pytest.param("Two remain: (AC4, AC5)", "nickname", id="bracketed-list-short-words"),
        pytest.param("(see AC4)", "bare", id="aside"),
        pytest.param("[AC4] is open", "bare", id="square-brackets"),
        pytest.param("AC4 — gloss", "bare", id="number-then-gloss"),
        pytest.param(f"{AC4} (AC4)\n\nThe override item (AC4) is still open", "text-with-number", id="pointer-back"),
        pytest.param("The override item (AC4) is still open", "nickname", id="no-earlier-statement"),
    ],
)
def test_worked_cases_of_the_fixed_grammar(text, verdict):
    assert mention(text) == verdict


@pytest.mark.parametrize(
    "text",
    [
        "criterion 3 is done, item 3 too, see #3, ac3, AC and XAC3.",
        "Ten pounds, three days, 12 findings.",
        "\n\n  \n",
    ],
)
def test_text_that_names_no_criterion_number_is_no_mention(text):
    assert mention(text) == "no-mention"


def test_number_is_not_a_criterion_number_when_glued_to_letters_or_more_digits():
    assert mention("The thing (AC40)") == "nickname"
    assert mention("XAC4 is open") == "no-mention"
    assert mention("AC4x is open") == "bare"


# --- criterion-mention: words before, window, share ----------------------------------------


def test_share_of_own_words_decides_at_exactly_half(small):
    assert mention("alpha beta (AC1)", criteria=small) == "text-with-number"
    assert mention("alpha (AC1)", criteria=small) == "nickname"
    assert mention("alpha beta gamma (AC1)", criteria=small) == "text-with-number"


def test_only_words_of_four_or_more_letters_are_a_criterions_own_words(tmp_path):
    path = tmp_path / "c.md"
    path.write_text("**AC1.** cat dog bird fish\n", encoding="utf-8")
    assert mention("bird (AC1)", criteria=path) == "text-with-number"
    assert mention("cat dog (AC1)", criteria=path) == "nickname"


def test_a_criterion_continues_over_following_lines_to_the_next_blank_line(tmp_path):
    path = tmp_path / "c.md"
    path.write_text("**AC1.** alpha beta\ngamma delta\n\nepsilon zeta\n", encoding="utf-8")
    assert mention("gamma delta (AC1)", criteria=path) == "text-with-number"
    assert mention("epsilon zeta (AC1)", criteria=path) == "nickname"


def test_criteria_are_read_from_list_item_lines_too(tmp_path):
    path = tmp_path / "c.md"
    path.write_text("- **AC1.** alpha beta\n- **AC2.** gamma delta\n", encoding="utf-8")
    assert mention("gamma delta (AC2)", criteria=path) == "text-with-number"


def test_plural_s_is_folded_on_both_sides(tmp_path):
    plural = tmp_path / "p.md"
    plural.write_text("**AC1.** books pages shelves tables\n", encoding="utf-8")
    singular = tmp_path / "s.md"
    singular.write_text("**AC1.** book page shelf table\n", encoding="utf-8")
    assert mention("book page (AC1)", criteria=plural) == "text-with-number"
    assert mention("books pages (AC1)", criteria=singular) == "text-with-number"
    assert mention("bookcase pager (AC1)", criteria=singular) == "nickname"


def test_words_are_compared_case_insensitively(small):
    assert mention("ALPHA Beta (AC1)", criteria=small) == "text-with-number"


def test_a_number_missing_from_the_criteria_file_cannot_pass(small):
    assert mention("alpha beta gamma delta kappa omega (AC9)", criteria=small) == "nickname"


@pytest.mark.parametrize("terminator", [". ", "! ", "? ", "; "])
def test_words_before_the_last_sentence_terminator_are_cut(small, terminator):
    assert mention(f"alpha beta{terminator}gamma (AC1)", criteria=small) == "nickname"


def test_a_terminator_not_followed_by_a_space_does_not_cut(small):
    assert mention("alpha beta.gamma (AC1)", criteria=small) == "text-with-number"


@pytest.mark.parametrize("tail", [".", "!", "?", ";", ",", ":", '"', "'", "-", "\u2013", "\u2014"])
def test_trailing_punctuation_is_stripped_before_the_sentence_cut(small, tail):
    assert mention(f"alpha beta. {tail} (AC1)", criteria=small) == "text-with-number"
    assert mention(f"alpha beta{tail} (AC1)", criteria=small) == "text-with-number"


def test_only_the_last_sixty_words_count(small):
    filler = " ".join(["zed"] * 58)
    assert mention(f"alpha beta {filler} (AC1)", criteria=small) == "text-with-number"
    assert mention(f"alpha beta {filler} zed (AC1)", criteria=small) == "nickname"


def test_spaces_inside_the_brackets_do_not_change_the_group(small):
    assert mention("alpha beta ( AC1 )", criteria=small) == "text-with-number"


def test_markup_characters_are_deleted_before_reading(small):
    assert mention("**alpha** `beta` (**AC1**)", criteria=small) == "text-with-number"
    assert mention("alpha beta (`AC1`)", criteria=small) == "text-with-number"
    assert mention("al_pha beta (AC1)", criteria=small) == "text-with-number"


@pytest.mark.parametrize("inside", ["(AC1: gloss)", "(see AC1)", "(AC1 gloss)", "(AC1;)", "[AC1]", "(AC1]", "[AC1)"])
def test_anything_else_between_the_brackets_makes_the_number_bare(small, inside):
    assert mention(f"alpha beta {inside}", criteria=small) == "bare"


def test_text_after_the_brackets_is_not_read_as_words_before(small):
    assert mention("zed (AC1) alpha beta gamma delta", criteria=small) == "nickname"


# --- criterion-mention: groups -------------------------------------------------------------


@pytest.mark.parametrize(
    "joiner",
    [", ", ",", " and ", ", and ", " or ", " & ", " &", "/", " / ", " + ", "+"],
)
def test_list_connectors_join_numbers_into_one_group(small, joiner):
    words = "alpha beta gamma delta"
    assert mention(f"{words} (AC1{joiner}AC2)", criteria=small) == "text-with-number"
    assert mention(f"{words} AC1{joiner}AC2", criteria=small) == "bare"


def test_a_bracketed_list_needs_every_number_to_clear_the_share(small):
    assert mention("alpha beta gamma delta (AC1, AC2)", criteria=small) == "text-with-number"
    assert mention("alpha beta (AC1, AC2)", criteria=small) == "nickname"
    assert mention("alpha beta gamma delta (AC2, AC1)", criteria=small) == "text-with-number"


@pytest.mark.parametrize("joiner", ["-", " - ", "–", "—", " to ", " through "])
def test_range_connectors_stand_for_every_number_between(small, joiner):
    ends = "alpha beta kappa omega"
    middle = "gamma delta"
    assert mention(f"{ends} {middle} (AC1{joiner}AC3)", criteria=small) == "text-with-number"
    assert mention(f"{ends} (AC1{joiner}AC3)", criteria=small) == "nickname"


def test_a_range_may_end_in_bare_digits(small):
    assert mention("alpha beta gamma delta kappa omega (AC1 to 3)", criteria=small) == "text-with-number"
    assert mention("alpha beta kappa omega (AC1-3)", criteria=small) == "nickname"
    assert mention("alpha beta kappa omega AC1-3", criteria=small) == "bare"


def test_a_descending_range_stands_for_just_its_two_ends(small):
    assert mention("alpha beta kappa omega (AC3 to AC1)", criteria=small) == "text-with-number"
    assert mention("alpha beta kappa omega (AC1 to AC3)", criteria=small) == "nickname"


def test_a_huge_range_does_not_expand_and_cannot_pass(small):
    assert mention("alpha beta (AC1 to 999999999999)", criteria=small) == "nickname"


def test_a_group_whose_numbers_all_appeared_earlier_passes_with_any_words_before(small):
    first = "alpha beta (AC1)\n\n"
    assert mention(first + "zed (AC1)", criteria=small) == "text-with-number"
    assert mention("zed (AC1)", criteria=small) == "nickname"
    assert mention(first + "zed (AC1, AC2)", criteria=small) == "nickname"
    assert mention(first + "alpha beta gamma delta (AC1, AC2)", criteria=small) == "text-with-number"


def test_a_pointer_back_still_needs_words_before(small):
    assert mention("alpha beta (AC1)\n\n(AC1)", criteria=small) == "bare"


def test_an_earlier_nickname_stays_a_nickname(small):
    assert mention("zed (AC1)\n\nzed again (AC1)", criteria=small) == "nickname"


def test_nickname_is_below_bare_in_the_capture_verdict(small):
    assert mention("zed (AC1)\n\nalpha beta (AC2)\n\nAC3 stands alone", criteria=small) == "bare"
    assert mention("zed (AC1)\n\ngamma delta (AC2)", criteria=small) == "nickname"


# --- criterion-mention: how the capture is read --------------------------------------------


def test_a_sentence_wrapped_over_physical_lines_is_one_logical_line(small):
    assert mention("alpha beta\n(AC1)", criteria=small) == "text-with-number"
    assert mention("alpha\nbeta gamma\ndelta (AC1)", criteria=small) == "text-with-number"


def test_a_blank_line_ends_the_logical_line(small):
    assert mention("alpha beta\n\n(AC1)", criteria=small) == "bare"


@pytest.mark.parametrize(
    "start",
    ["- ", "* ", "+ ", "1. ", "12) "],
)
def test_a_list_item_starts_its_own_logical_line(small, start):
    assert mention(f"alpha beta\n{start}(AC1)", criteria=small) == "bare"
    assert mention(f"{start}alpha beta (AC1)", criteria=small) == "text-with-number"


def test_a_table_row_starts_its_own_logical_line(small):
    assert mention("alpha beta\n| (AC1) |", criteria=small) == "bare"


def test_a_table_is_read_cell_by_cell(small):
    table = "| Item | Status |\n| --- | :---: |\n| alpha beta (AC1) | open |\n"
    assert mention(table, criteria=small) == "text-with-number"
    assert mention("| alpha beta | (AC1) |", criteria=small) == "bare"
    assert mention("| alpha beta | AC1 |", criteria=small) == "bare"


def test_a_table_separator_row_is_not_a_cell_to_grade(small):
    assert mention("| a | b |\n| --- | --- |\n", criteria=small) == "no-mention"


def test_windows_never_cross_a_heading_line_start(small):
    assert mention("alpha beta\n# (AC1)", criteria=small) == "bare"


def test_crlf_line_endings_read_like_lf(small):
    assert mention("alpha beta\r\n\r\n(AC1)", criteria=small) == "bare"
    assert mention("alpha\r\nbeta (AC1)", criteria=small) == "text-with-number"


def test_unicode_line_separators_do_not_split_lines(small):
    assert mention("alpha beta\u2028\u2028(AC1)", criteria=small) == "text-with-number"


# --- criterion-mention over a capture that ends in the covered-criteria value ------------------


def test_final_line_with_a_criterion_number_is_left_out_of_the_statement_on_request(small):
    capture = "alpha beta (AC1)\n\nAC1, AC2\n\n"
    assert mention(capture, criteria=small) == "bare"
    assert mention(capture, criteria=small, extra=["--drop-value-line"]) == "text-with-number"


def test_only_the_last_nonblank_line_is_left_out(small):
    capture = "AC3 stands alone here\n\nalpha beta (AC1)\nAC1, AC2"
    assert mention(capture, criteria=small, extra=["--drop-value-line"]) == "bare"


def test_a_final_line_of_prose_with_a_number_is_left_out_too(small):
    capture = "alpha beta (AC1)\n\nthen a final bare AC2"
    assert mention(capture, criteria=small) == "bare"
    assert mention(capture, criteria=small, extra=["--drop-value-line"]) == "text-with-number"


def test_dropping_the_value_line_applies_to_the_statement_check_only():
    could_not_grade(
        ["--check", "covers-field", "--drop-value-line"], b"AC1, AC2\n", "option-not-applicable"
    )


# --- covers-field --------------------------------------------------------------------------


def covers(text):
    return grade("covers-field", text, criteria=None)


@pytest.mark.parametrize(
    "last_line, verdict",
    [
        pytest.param("AC3, AC4", "gate-shape", id="bare-list"),
        pytest.param("AC3,AC4", "gate-shape", id="no-space"),
        pytest.param("AC3", "gate-shape", id="single"),
        pytest.param("  AC3, AC4  ", "gate-shape", id="surrounding-whitespace"),
        pytest.param("**Covers:** AC3, AC4", "gate-shape", id="field-label"),
        pytest.param("**Covers:**AC3", "gate-shape", id="field-label-no-space"),
        pytest.param("**Covers:**   AC3, AC4", "gate-shape", id="field-label-many-spaces"),
        pytest.param("A borrower owing over ten pounds cannot check out (AC3)", "off-shape", id="text-and-brackets"),
        pytest.param("AC3 and AC4", "off-shape", id="and"),
        pytest.param("AC3, AC4.", "off-shape", id="trailing-full-stop"),
        pytest.param("AC3; AC4", "off-shape", id="semicolon"),
        pytest.param("`AC3, AC4`", "off-shape", id="backticks"),
        pytest.param("Covers: AC3, AC4", "off-shape", id="plain-label"),
        pytest.param("**Partially covers:** AC3", "off-shape", id="other-label"),
        pytest.param("note **Covers:** AC3", "off-shape", id="label-not-at-start"),
        pytest.param("**Covers:** **Covers:** AC3", "off-shape", id="label-twice"),
        pytest.param("**Covers:** `AC3, AC4`", "off-shape", id="label-then-backticks"),
        pytest.param("**Covers:** AC3 (the checkout block)", "off-shape", id="label-then-text"),
        pytest.param("**Covers:**", "off-shape", id="label-alone"),
        pytest.param("AC3, AC3", "off-shape", id="repeated-identifier"),
        pytest.param("AC3,  AC4", "off-shape", id="two-spaces"),
        pytest.param("(AC3)", "off-shape", id="bracketed"),
        pytest.param("The change covers nothing numbered.", "absent", id="no-number"),
        pytest.param("ac3, ac4", "absent", id="lowercase-is-no-number"),
        pytest.param("**Covers:** none", "absent", id="label-then-no-number"),
    ],
)
def test_covers_field_reads_the_last_nonblank_line(last_line, verdict):
    assert covers(f"Some statement mentioning AC1 and AC2 up top.\n\n{last_line}\n\n  \n") == verdict


def test_covers_field_of_a_blank_capture_is_absent():
    assert covers("  \n\t\n") == "absent"


def test_covers_field_ignores_earlier_lines_entirely():
    assert covers("AC1, AC2\n\nnothing at the end") == "absent"
    assert covers("nothing here\nAC1, AC2") == "gate-shape"
    assert covers("Intro.\r\nAC1, AC2\r\n") == "gate-shape"


def test_covers_field_needs_no_criteria_file():
    result = run_grader(["--check", "covers-field"], b"AC1\n")
    assert result.returncode == 0
    assert result.stdout == b"covers-field: gate-shape\n"


# --- reviewer-report -----------------------------------------------------------------------

REPORT = (
    "## Findings\n\n"
    "- [Critical] No test contract. Suggested: add one.\n"
    "- [Minor] Naming.\n\n"
    "## Confidence\n"
    "Medium, no codebase to check.\n"
)


def report(text):
    return grade("reviewer-report", text, criteria=None)


def missing(text):
    result = run_grader(["--check", "reviewer-report"], text.encode("utf-8"))
    assert result.stdout == b"reviewer-report: off-shape\n"
    assert result.returncode == 1
    return result.stderr.decode("utf-8")


def test_template_headings_and_a_tagged_finding_are_template_shape():
    assert report(REPORT) == "template-shape"


def test_without_the_findings_heading_the_report_is_off_shape_and_says_so():
    assert "missing: findings-heading" in missing(REPORT.replace("## Findings\n", ""))


def test_without_any_tagged_finding_line_the_report_is_off_shape_and_says_so():
    text = REPORT.replace("[Critical]", "Critical:").replace("[Minor]", "Minor:")
    assert "missing: tagged-finding" in missing(text)


def test_without_the_confidence_heading_the_report_is_off_shape_and_says_so():
    assert "missing: confidence-heading" in missing(REPORT.replace("## Confidence\n", ""))


def test_a_confidence_heading_with_nothing_after_it_is_off_shape():
    assert "missing: confidence-text" in missing(REPORT.split("Medium")[0] + "\n\n")


@pytest.mark.parametrize("tag", ["[Critical]", "[Important]", "[Minor]"])
@pytest.mark.parametrize("marker", ["- ", "* ", "  - ", "- **", "* **", "", "  "])
def test_each_severity_tag_after_any_list_marker_counts(tag, marker):
    text = f"## Findings\n{marker}{tag} something.\n## Confidence\nHigh.\n"
    assert report(text) == "template-shape"


def test_a_tag_that_does_not_start_the_line_does_not_count():
    text = "## Findings\n- see [Critical] above\n- Note: [Minor] x\n## Confidence\nHigh.\n"
    assert "missing: tagged-finding" in missing(text)


def test_an_unknown_severity_tag_does_not_count():
    assert "missing: tagged-finding" in missing("## Findings\n- [Blocker] x\n## Confidence\nHigh.\n")


def test_tagged_lines_outside_the_two_headings_do_not_count():
    before = "- [Critical] before\n## Findings\nnothing\n## Confidence\nHigh.\n"
    after = "## Findings\nnothing\n## Confidence\n- [Critical] after\n"
    assert "missing: tagged-finding" in missing(before)
    assert "missing: tagged-finding" in missing(after)


def test_confidence_before_findings_is_off_shape():
    text = "## Confidence\nHigh.\n## Findings\n- [Critical] x\n"
    assert "missing: confidence-heading" in missing(text)


def test_text_before_the_findings_heading_is_allowed():
    assert report("Preamble.\n\n" + REPORT) == "template-shape"


def test_heading_lines_are_matched_exactly_after_trimming():
    assert report(REPORT.replace("## Findings", "   ## Findings   ")) == "template-shape"
    assert "missing: findings-heading" in missing(REPORT.replace("## Findings", "### Findings"))
    assert "missing: findings-heading" in missing(REPORT.replace("## Findings", "## Findings:"))
    assert "missing: confidence-heading" in missing(REPORT.replace("## Confidence", "## Confidence level"))


def test_exceeding_the_templates_caps_is_still_template_shape():
    many = "".join(f"- [Critical] finding {i}\n" for i in range(5))
    assert report(f"## Findings\n{many}## Confidence\nLow. " + "word " * 900) == "template-shape"


def test_reviewer_report_needs_no_criteria_file():
    result = run_grader(["--check", "reviewer-report"], REPORT.encode())
    assert result.returncode == 0
    assert result.stdout == b"reviewer-report: template-shape\n"


# --- could not grade -----------------------------------------------------------------------

CHECK_ARGS = ["--check", "criterion-mention", "--criteria", str(SPEC)]


def test_empty_stdin_could_not_grade_for_every_check():
    for check in ("criterion-mention", "covers-field", "reviewer-report"):
        could_not_grade(["--check", check, "--criteria", str(SPEC)], b"", "empty-input")


def test_whitespace_only_stdin_is_a_capture_and_gets_a_verdict():
    assert mention(" \n\n") == "no-mention"
    assert covers(" \n") == "absent"
    assert report("\n") == "off-shape"


def test_unknown_check_name():
    could_not_grade(["--check", "nonsense"], b"x\n", "unknown-check")
    could_not_grade(["--check", "criterion_mention"], b"x\n", "unknown-check")


def test_check_name_is_required():
    could_not_grade([], b"x\n", "missing-check")
    could_not_grade(["--criteria", str(SPEC)], b"x\n", "missing-check")


def test_unrecognised_option_could_not_grade():
    could_not_grade(CHECK_ARGS + ["--frobnicate"], b"x\n", "bad-arguments")


def test_non_utf8_stdin():
    could_not_grade(CHECK_ARGS, b"alpha \xff\xfe beta (AC1)\n", "not-utf8")
    could_not_grade(["--check", "covers-field"], b"AC1\xc3\x28\n", "not-utf8")


def test_input_over_the_size_bound_is_refused_and_the_bound_is_inclusive():
    limit = _load_module().MAX_INPUT_BYTES
    exactly = (b"a" * (limit - 1)) + b"\n"
    assert len(exactly) == limit
    assert run_grader(CHECK_ARGS, exactly).returncode == 1
    could_not_grade(CHECK_ARGS, exactly + b"a", "input-too-large")
    could_not_grade(["--check", "covers-field"], b"a" * (limit + 1), "input-too-large")


def test_size_bound_counts_bytes_not_characters():
    limit = _load_module().MAX_INPUT_BYTES
    two_byte = "é" * (limit // 2 + 1)
    assert len(two_byte) <= limit < len(two_byte.encode("utf-8"))
    could_not_grade(CHECK_ARGS, two_byte.encode("utf-8"), "input-too-large")


def test_criterion_mention_without_a_criteria_argument():
    could_not_grade(["--check", "criterion-mention"], b"x (AC1)\n", "missing-criteria")


def test_criteria_file_that_does_not_exist(tmp_path):
    could_not_grade(
        ["--check", "criterion-mention", "--criteria", str(tmp_path / "gone.md")], b"x\n", "criteria-unreadable"
    )


def test_criteria_path_that_is_a_directory(tmp_path):
    could_not_grade(["--check", "criterion-mention", "--criteria", str(tmp_path)], b"x\n", "criteria-unreadable")


def test_criteria_file_that_is_not_utf8(tmp_path):
    path = tmp_path / "c.md"
    path.write_bytes(b"**AC1.** caf\xe9 alpha\n")
    could_not_grade(["--check", "criterion-mention", "--criteria", str(path)], b"x\n", "criteria-not-utf8")


def test_criteria_file_with_no_criterion_lines(tmp_path):
    path = tmp_path / "c.md"
    path.write_text("# A spec\n\nAC1. alpha beta\n- AC2: gamma\n", encoding="utf-8")
    could_not_grade(["--check", "criterion-mention", "--criteria", str(path)], b"x\n", "no-criteria-found")


def test_criteria_file_naming_one_criterion_twice(tmp_path):
    path = tmp_path / "c.md"
    path.write_text("**AC1.** alpha\n\n**AC1.** beta\n", encoding="utf-8")
    could_not_grade(["--check", "criterion-mention", "--criteria", str(path)], b"x\n", "duplicate-criterion")


def test_criteria_argument_is_not_read_by_the_checks_that_do_not_need_it(tmp_path):
    result = run_grader(["--check", "covers-field", "--criteria", str(tmp_path / "gone.md")], b"AC1\n")
    assert result.returncode == 0


def test_every_verdict_exits_with_its_class():
    cases = [
        ("criterion-mention", f"{AC4} (AC4)", 0),
        ("criterion-mention", "zed (AC4)", 1),
        ("criterion-mention", "AC4 stands", 1),
        ("criterion-mention", "none", 1),
        ("covers-field", "AC1", 0),
        ("covers-field", "AC1 and AC2", 1),
        ("covers-field", "none", 1),
        ("reviewer-report", REPORT, 0),
        ("reviewer-report", "nothing", 1),
    ]
    for check, text, code in cases:
        result = run_grader(["--check", check, "--criteria", str(SPEC)], text.encode())
        assert result.returncode == code, (check, text, result.stdout)


# --- pattern safety ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, build",
    [
        pytest.param("many-groups", lambda n: "(AC1)" * (n // 5), id="many-groups"),
        pytest.param("many-list-members", lambda n: "(AC1" + ", AC2" * (n // 5) + ")", id="long-list"),
        pytest.param("long-range-chain", lambda n: "(AC1" + "-1" * (n // 2) + ")", id="range-chain"),
        pytest.param("whitespace-runs", lambda n: "AC1" + " " * n + "AC2", id="spaces"),
        pytest.param("spaced-numbers", lambda n: ("AC1" + " " * 50) * (n // 53), id="spaced-numbers"),
        pytest.param("open-brackets", lambda n: "(" * n + "AC1", id="open-brackets"),
        pytest.param("terminators", lambda n: "a. " * (n // 3) + "(AC1)", id="terminators"),
        pytest.param("words", lambda n: "w " * (n // 2) + "(AC1)", id="words"),
        pytest.param("one-line-cells", lambda n: "|" + "a (AC1) |" * (n // 9), id="cells"),
        pytest.param("wrapped", lambda n: "a (AC1)\n" * (n // 8), id="wrapped"),
    ],
)
def test_hostile_input_at_the_size_bound_grades_promptly(small, name, build):
    limit = _load_module().MAX_INPUT_BYTES
    text = build(limit)[:limit].encode("utf-8")
    result = run_grader(["--check", "criterion-mention", "--criteria", str(small)], text, timeout=30)
    assert result.returncode in (0, 1), (name, result.stderr)
    assert result.stdout.startswith(b"criterion-mention: ")


def test_long_covers_line_grades_promptly():
    limit = _load_module().MAX_INPUT_BYTES
    value = ",".join(f"AC{i}" for i in range(limit // 8))[: limit - 2] + "x"
    result = run_grader(["--check", "covers-field"], value.encode("utf-8"), timeout=30)
    assert result.stdout == b"covers-field: off-shape\n"


# --- the committed captures ----------------------------------------------------------------

# Each capture prefix names its checks. An entry is (check, extra arguments, expected verdict);
# `--drop-value-line` is how a capture that ends in the covered-criteria value is graded without that final line.
# Every check runs with `--criteria` set to the case's spec fixture.
# A verdict is the grader's reading of a capture under the thresholds fixed before any run; where a capture read by eye
# differs, CAPTURE_OVERRIDES records the grader's verdict.
CAPTURE_EXPECTATIONS = {
    "baseline-noritual": [("criterion-mention", [], "bare")],
    "baseline-slice": [
        ("criterion-mention", ["--drop-value-line"], "bare"),
        ("covers-field", [], "gate-shape"),
    ],
    "baseline-reviewer": [
        ("reviewer-report", [], "template-shape"),
        ("criterion-mention", [], "bare"),
    ],
    "treated-noritual": [("criterion-mention", [], "text-with-number")],
    "treated-slice-rules-only": [
        ("criterion-mention", ["--drop-value-line"], "text-with-number"),
        ("covers-field", [], "gate-shape"),
    ],
    "treated-reviewer": [
        ("reviewer-report", [], "template-shape"),
        ("criterion-mention", [], "nickname"),
    ],
    "second-noritual": [("criterion-mention", [], "text-with-number")],
    "second-slice-rules-only": [
        ("criterion-mention", ["--drop-value-line"], "text-with-number"),
        ("covers-field", [], "gate-shape"),
    ],
    "second-reviewer": [
        ("reviewer-report", [], "template-shape"),
        ("criterion-mention", [], "nickname"),
    ],
    "third-noritual": [("criterion-mention", [], "text-with-number")],
    "third-slice-rules-only": [
        ("criterion-mention", ["--drop-value-line"], "text-with-number"),
        ("covers-field", [], "gate-shape"),
    ],
    "shipped-slice": [
        ("criterion-mention", ["--drop-value-line"], "text-with-number"),
        ("covers-field", [], "gate-shape"),
    ],
    "third-reviewer-rules-only": [
        ("reviewer-report", [], "template-shape"),
        ("criterion-mention", [], "nickname"),
    ],
    "third-reviewer": [
        ("reviewer-report", [], "template-shape"),
        ("criterion-mention", [], "bare"),
    ],
    "baseline-advocate": [
        ("reviewer-report", [], "template-shape"),
        ("criterion-mention", [], "bare"),
    ],
    "third-advocate": [
        ("reviewer-report", [], "template-shape"),
        ("criterion-mention", [], "bare"),
    ],
}

# A capture whose verdict differs from its prefix's is listed here by stem, with the verdict per check.
# Each entry records the grader's verdict, under the thresholds fixed before any run, and those thresholds are the ones
# that count; read by eye, a capture may look like `text-with-number` or a combination of forms instead.
CAPTURE_OVERRIDES = {
    "treated-noritual-1": {"criterion-mention": "nickname"},
    "treated-noritual-2": {"criterion-mention": "nickname"},
    "second-noritual-2": {"criterion-mention": "nickname"},
    "second-noritual-3": {"criterion-mention": "nickname"},
    "second-noritual-6": {"criterion-mention": "nickname"},
    "second-reviewer-3": {"criterion-mention": "bare"},
    "second-reviewer-4": {"criterion-mention": "bare"},
    "third-reviewer-rules-only-1": {"criterion-mention": "bare"},
    "third-reviewer-rules-only-3": {"criterion-mention": "bare"},
}

CAPTURES = sorted(p for p in RUNS.glob("*.txt") if not p.name.endswith(".stderr.txt"))


def _prefix(path):
    return re.fullmatch(r"(.+)-\d+\.txt", path.name).group(1)


@pytest.mark.parametrize("capture", CAPTURES, ids=lambda p: p.stem)
def test_every_capture_grades_to_the_verdict_its_prefix_is_mapped_to(capture):
    prefix = _prefix(capture)
    assert prefix in CAPTURE_EXPECTATIONS, f"{capture.name} has no mapped prefix {prefix!r}"
    for check, extra, verdict in CAPTURE_EXPECTATIONS[prefix]:
        verdict = CAPTURE_OVERRIDES.get(capture.stem, {}).get(check, verdict)
        args = ["--check", check, "--criteria", str(SPEC), *extra]
        result = run_grader(args, capture.read_bytes())
        assert result.stdout.decode("utf-8") == f"{check}: {verdict}\n", (capture.name, result.stderr)
        assert result.returncode == (0 if verdict in PASSING else 1)


# --- the verdict words document ------------------------------------------------------------


def test_expected_md_names_every_verdict_word_the_script_can_print():
    words = _load_module().VERDICT_WORDS
    section = EXPECTED_MD.read_text(encoding="utf-8").split("## Verdict words", 1)[1].split("\n## ", 1)[0]
    for check, verdicts in words.items():
        line = next(ln for ln in section.splitlines() if ln.startswith(f"- `{check}`:"))
        named = set(re.findall(r"`([a-z-]+)`", line.split(":", 1)[1]))
        assert named == set(verdicts), (check, named)
    assert len(words) == 3
