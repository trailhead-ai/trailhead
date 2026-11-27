"""The verdict step of the red-then-green host scripts, run over recorded pytest output (no pytest run here)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import pytest

from ._gateway_e2e_support import red_verdict

SETUP_ERROR = (
    "the run scenario cannot start on this host:\n"
    "- there is no outpost-run group: run sudo scripts/provision-outpost-runner from the dotfiles\n"
    "- your home is 0755, which the run account could read: chmod 0750 it"
)
MARKER = "invalid choice: 'start'"


def _junit(failures: list[str] = (), errors: list[str] = (), passed: list[str] = ()) -> str:
    cases = [f'<testcase name="t_fail{i}"><failure message={quoteattr(m)}>{escape(m)}</failure></testcase>' for i, m in enumerate(failures)]
    cases += [f'<testcase name="t_err{i}"><error message={quoteattr(m.splitlines()[0])}>{escape(m)}</error></testcase>' for i, m in enumerate(errors)]
    cases += [f'<testcase name="{n}"/>' for n in passed]
    return f'<?xml version="1.0"?><testsuites><testsuite name="pytest" errors="{len(errors)}" failures="{len(failures)}">{"".join(cases)}</testsuite></testsuites>'


def test_setup_errors_name_the_first_setup_check_that_failed():
    xml = _junit(errors=[SETUP_ERROR] * 3, failures=["x"])
    assert red_verdict(xml, "", expect_fail=5, markers=(MARKER,)) == (
        False,
        "setup failed: there is no outpost-run group: run sudo scripts/provision-outpost-runner from the dotfiles",
    )


def test_an_error_that_is_not_a_setup_report_is_named_by_its_own_first_line():
    xml = _junit(errors=["RuntimeError: outpost did not boot\ntraceback"])
    assert red_verdict(xml, "", expect_fail=5, markers=(MARKER,)) == (False, "setup failed: RuntimeError: outpost did not boot")


def test_exactly_the_expected_failures_with_the_marker_are_a_valid_red():
    xml = _junit(failures=["f"] * 5, passed=["a", "b"])
    assert red_verdict(xml, f"E  argparse: {MARKER}", expect_fail=5, markers=(MARKER,)) == (True, "red valid")


def test_the_expected_failures_without_the_marker_are_invalid_with_their_counts():
    xml = _junit(failures=["f"] * 5, passed=["a", "b"])
    ok, text = red_verdict(xml, "nothing relevant", expect_fail=5, markers=(MARKER,))
    assert not ok and text.startswith("invalid") and "5 failures" in text and "0 errors" in text and MARKER in text


@pytest.mark.parametrize("failures", [4, 6])
def test_one_failure_either_side_of_the_expected_count_is_invalid(failures):
    ok, text = red_verdict(_junit(failures=["f"] * failures), MARKER, expect_fail=5, markers=(MARKER,))
    assert not ok and f"{failures} failures, expected 5" in text


def test_each_failure_must_carry_one_of_the_signals_when_asked():
    xml = _junit(failures=["invalid choice: 'cancel'", "boom"], passed=["test_no_secret_or_token"])
    ok, text = red_verdict(xml, "", expect_fail=2, markers=("invalid choice: 'cancel'",), per_failure=True, allow_pass=("no_secret_or_token",))
    assert not ok and "t_fail1" in text
    assert "2 failures" in text and "0 errors" in text


@pytest.mark.parametrize("where", ["message", "text"])
def test_a_failure_carries_its_signal_in_either_its_message_or_its_body(where):
    signal = "invalid choice: 'cancel'"
    body = {"message": f'message={quoteattr(signal)}>body', "text": f'message="short">{escape(signal)}'}[where]
    xml = f'<testsuites><testsuite><testcase name="t_a"><failure {body}</failure></testcase></testsuite></testsuites>'
    assert red_verdict(xml, "", expect_fail=1, markers=(signal,), per_failure=True) == (True, "red valid")


def test_a_passing_test_that_is_not_allowed_to_pass_is_invalid_when_each_failure_is_checked():
    xml = _junit(failures=["invalid choice: 'cancel'"], passed=["test_other"])
    ok, text = red_verdict(xml, "", expect_fail=1, markers=("invalid choice: 'cancel'",), per_failure=True, allow_pass=("no_secret_or_token",))
    assert not ok and "test_other" in text


def test_every_failure_carrying_a_signal_with_only_the_allowed_pass_is_valid():
    xml = _junit(failures=["unrecognized arguments: --log", "invalid choice: 'cancel'"], passed=["test_no_secret_or_token"])
    assert red_verdict(
        xml, "", expect_fail=2, markers=("invalid choice: 'cancel'", "unrecognized arguments: --log"), per_failure=True, allow_pass=("no_secret_or_token",)
    ) == (True, "red valid")


def test_the_command_prints_the_verdict_and_exits_by_it(tmp_path):
    def run(xml: str):
        report = tmp_path / "red.xml"
        report.write_text(xml)
        log = tmp_path / "red.log"
        log.write_text(MARKER)
        return subprocess.run(
            [sys.executable, "-m", "trailhead.tests._gateway_e2e_support", "red-verdict", "--xml", str(report), "--log", str(log), "--expect-fail", "2", "--marker", MARKER],
            capture_output=True, text=True, cwd=Path(__file__).resolve().parents[2],
        )

    good = run(_junit(failures=["f", "f"]))
    assert (good.returncode, good.stdout.strip()) == (0, "red valid")
    bad = run(_junit(errors=[SETUP_ERROR]))
    assert bad.returncode == 1 and bad.stdout.strip().startswith("setup failed: there is no outpost-run group")
    missing = subprocess.run(
        [sys.executable, "-m", "trailhead.tests._gateway_e2e_support", "red-verdict", "--xml", str(tmp_path / "none.xml"), "--log", str(tmp_path / "none.log"), "--expect-fail", "2", "--marker", MARKER],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[2],
    )
    assert missing.returncode == 1 and missing.stdout.startswith("invalid: no pytest report")
