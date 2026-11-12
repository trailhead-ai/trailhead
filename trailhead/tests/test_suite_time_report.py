"""`scripts/suite-time-report` run against generated JUnit XML.

Each case writes the XML a pytest run would write (xunit1: testcases carry `file`)
and runs the real script as a subprocess, asserting on exit code and output.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "suite-time-report"

MARKER = "SECRET-CAPTURED-OUTPUT-7f3a"


def _case(name, time, file="trailhead/tests/test_a.py", outcome="passed", out=None):
    inner = ""
    if outcome == "skipped":
        inner = '<skipped type="pytest.skip" message="nope">skip</skipped>'
    elif outcome == "failed":
        inner = '<failure message="boom">boom</failure>'
    if out:
        inner += f"<system-out>{out}</system-out>"
    return (
        f'<testcase classname="mod" name="{name}" file="{file}" line="1" '
        f'time="{time}">{inner}</testcase>'
    )


def _xml(cases, wall):
    return (
        '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">'
        f'<testsuite name="pytest" errors="0" failures="0" skipped="0" tests="{len(cases)}" '
        f'time="{wall}" timestamp="2026-01-01T00:00:00" hostname="h">{"".join(cases)}'
        "</testsuite></testsuites>"
    )


def _run(tmp_path, cases, wall, *args, area=("trailhead/tests", "tools/lore/tests")):
    xml = tmp_path / "junit.xml"
    xml.write_text(_xml(cases, wall), encoding="utf-8")
    return _run_file(xml, *args, area=area)


def _run_file(xml, *args, area=("trailhead/tests", "tools/lore/tests"), github_actions=False):
    area_args = [x for a in area for x in ("--area", a)]
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_ACTIONS"}
    if github_actions:
        env["GITHUB_ACTIONS"] = "true"
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(xml), *area_args, *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _out(r):
    return r.stdout + r.stderr


def test_under_budget_exits_zero(tmp_path):
    r = _run(tmp_path, [_case("t1", 1.0)], 99.0, "--budget", "100")
    assert r.returncode == 0, _out(r)
    assert "OK" in r.stdout


def test_one_second_over_budget_warns_names_the_budget_and_exits_zero(tmp_path):
    r = _run(tmp_path, [_case("t1", 1.0)], 101.0, "--budget", "100")
    assert r.returncode == 0, _out(r)
    first = r.stdout.splitlines()[0]
    assert "WARN" in first and "budget of 100.0s" in first and "101.0s" in first


def test_a_count_breach_fails_even_beside_a_budget_overrun_and_both_are_reported(tmp_path):
    r = _run(tmp_path, [_case("t1", 1.0)], 101.0, "--budget", "100", "--baseline-passed", "2")
    assert r.returncode == 1
    first = r.stderr.splitlines()[0]
    assert "FAIL" in first and "below the recorded baseline of 2" in first
    assert "budget of 100.0s" in r.stderr


@pytest.mark.parametrize("github_actions, annotated", [(True, True), (False, False)])
def test_a_timing_warning_is_annotated_only_under_github_actions(tmp_path, github_actions, annotated):
    xml = tmp_path / "junit.xml"
    xml.write_text(_xml([_case("slowpoke", 31.0)], 101.0), encoding="utf-8")
    r = _run_file(xml, "--budget", "100", "--per-test-cap", "30", "--tier", "slow",
                  github_actions=github_actions)
    assert r.returncode == 0, _out(r)
    notes = [ln for ln in r.stdout.splitlines() if ln.startswith("::warning")]
    if annotated:
        assert len(notes) == 2
        assert "budget of 100.0s" in notes[0] and "tier slow" in notes[0]
        assert "mod::slowpoke" in notes[1]
    else:
        assert notes == []


def test_exactly_at_budget_passes(tmp_path):
    r = _run(tmp_path, [_case("t1", 1.0)], 100.0, "--budget", "100")
    assert r.returncode == 0, _out(r)


def test_wall_time_is_the_testsuite_time_not_the_sum_of_tests(tmp_path):
    cases = [_case(f"t{i}", 10.0) for i in range(5)]
    r = _run(tmp_path, cases, 12.0, "--budget", "20")
    assert r.returncode == 0, _out(r)
    assert "12.0s" in r.stdout


def test_a_test_one_second_over_the_cap_warns_names_that_test_and_exits_zero(tmp_path):
    cases = [_case("fast", 1.0), _case("slowpoke", 31.0)]
    r = _run(tmp_path, cases, 40.0, "--per-test-cap", "30")
    assert r.returncode == 0, _out(r)
    assert "WARN" in r.stdout.splitlines()[0]
    assert "mod::slowpoke" in r.stdout and "per-test cap of 30.0s" in r.stdout
    assert "mod::fast took" not in r.stdout


def test_a_test_exactly_at_the_cap_passes(tmp_path):
    r = _run(tmp_path, [_case("t", 30.0)], 31.0, "--per-test-cap", "30")
    assert r.returncode == 0, _out(r)


def test_failure_text_names_owner_cap_semantics_and_repro(tmp_path):
    r = _run(tmp_path, [_case("t", 1.0)], 101.0, "--budget", "100", "--tier", "slow",
             "--repro-pytest-args", "-m slow")
    err = _out(r)
    assert ".github/workflows/tests.yml" in err
    assert "hung test still costs the job's timeout" in err
    assert "python -m pytest -m slow --junitxml=junit.xml" in err
    assert "--tier slow --budget 100" in err


def test_failure_text_shows_change_against_baseline_and_the_area_that_grew(tmp_path):
    cases = [_case("a", 10.0, "trailhead/tests/x.py"), _case("b", 50.0, "tools/lore/tests/y.py")]
    r = _run(tmp_path, cases, 70.0, "--budget", "60", "--baseline-seconds", "40",
             "--baseline-area", "trailhead/tests=9", "--baseline-area", "tools/lore/tests=20")
    assert "+30.0s" in _out(r)
    assert "Area that grew most: tools/lore/tests (+30.0s)" in _out(r)


def test_skipped_tests_are_counted_never_added_to_times(tmp_path):
    cases = [
        _case("ran", 2.0),
        _case("skipped", 500.0, outcome="skipped"),
        _case("bad", 3.0, outcome="failed"),
    ]
    r = _run(tmp_path, cases, 10.0, "--budget", "100", "--per-test-cap", "60")
    assert r.returncode == 0, _out(r)
    assert "collected 3, passed 1, failed 1, skipped 1" in r.stdout
    assert "trailhead/tests: 5.0s" in r.stdout
    assert "skipped" not in r.stdout.split("Slowest")[1]


def test_two_areas_produce_two_totals(tmp_path):
    cases = [
        _case("a1", 1.5, "trailhead/tests/a.py"),
        _case("a2", 2.0, "trailhead/tests/b.py"),
        _case("l1", 7.0, "tools/lore/tests/c.py"),
    ]
    r = _run(tmp_path, cases, 12.0)
    assert "trailhead/tests: 3.5s" in r.stdout
    assert "tools/lore/tests: 7.0s" in r.stdout


def test_area_falls_back_to_the_dotted_classname_without_a_file(tmp_path):
    xml = tmp_path / "j.xml"
    xml.write_text(
        '<testsuites><testsuite time="4"><testcase classname="trailhead.tests.test_x" name="t" time="4"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    r = _run_file(xml)
    assert "trailhead/tests: 4.0s" in r.stdout


def test_slowest_lists_n_tests_in_descending_order(tmp_path):
    cases = [_case(f"t{i}", float(i)) for i in range(1, 6)]
    r = _run(tmp_path, cases, 20.0, "--top", "2")
    lines = [ln for ln in r.stdout.splitlines() if "mod::t" in ln]
    assert [ln.split()[-1] for ln in lines] == ["mod::t5", "mod::t4"]


def test_passed_below_baseline_without_deletions_exits_nonzero(tmp_path):
    cases = [_case(f"t{i}", 1.0) for i in range(8)]
    r = _run(tmp_path, cases, 10.0, "--baseline-passed", "10")
    assert r.returncode == 1
    assert "2 below the recorded baseline of 10" in r.stderr
    assert "tier 'default'" in r.stderr


def test_the_same_drop_accounted_for_by_a_deletions_list_passes(tmp_path):
    cases = [_case(f"t{i}", 1.0) for i in range(8)]
    dels = tmp_path / "deleted.txt"
    dels.write_text("# removed\nmod::gone1\nmod::gone2\n", encoding="utf-8")
    r = _run(tmp_path, cases, 10.0, "--baseline-passed", "10", "--deletions", str(dels))
    assert r.returncode == 0, _out(r)


def test_a_deletions_list_that_covers_only_part_of_the_drop_fails(tmp_path):
    cases = [_case(f"t{i}", 1.0) for i in range(8)]
    dels = tmp_path / "deleted.txt"
    dels.write_text("mod::gone1\n", encoding="utf-8")
    r = _run(tmp_path, cases, 10.0, "--baseline-passed", "10", "--deletions", str(dels))
    assert r.returncode == 1
    assert "accounts for 1 of them" in r.stderr


def test_a_deletions_entry_that_still_ran_does_not_count(tmp_path):
    cases = [_case(f"t{i}", 1.0) for i in range(8)]
    dels = tmp_path / "deleted.txt"
    dels.write_text("mod::t0\nmod::t1\n", encoding="utf-8")
    r = _run(tmp_path, cases, 10.0, "--baseline-passed", "10", "--deletions", str(dels))
    assert r.returncode == 1


def test_passed_at_baseline_passes_and_tier_name_is_reported(tmp_path):
    cases = [_case(f"t{i}", 1.0) for i in range(3)]
    r = _run(tmp_path, cases, 5.0, "--baseline-passed", "3", "--tier", "slow")
    assert r.returncode == 0, _out(r)
    assert "Counts, tier slow: collected 3, passed 3" in r.stdout


def test_skipped_above_the_ceiling_exits_nonzero_naming_the_ceiling(tmp_path):
    cases = [_case("ok", 1.0), _case("s1", 0.0, outcome="skipped"), _case("s2", 0.0, outcome="skipped")]
    r = _run(tmp_path, cases, 5.0, "--max-skipped", "1", "--tier", "slow")
    assert r.returncode == 1
    assert "skipped count 2 is 1 above the recorded ceiling of 1 for tier 'slow'" in r.stderr


def test_skipped_at_the_ceiling_passes(tmp_path):
    cases = [_case("ok", 1.0), _case("s1", 0.0, outcome="skipped")]
    r = _run(tmp_path, cases, 5.0, "--max-skipped", "1")
    assert r.returncode == 0, _out(r)


def test_a_zero_ceiling_fails_on_the_first_skip(tmp_path):
    r = _run(tmp_path, [_case("ok", 1.0), _case("s1", 0.0, outcome="skipped")], 5.0, "--max-skipped", "0")
    assert r.returncode == 1
    assert "1 above the recorded ceiling of 0" in r.stderr


def test_without_a_ceiling_skips_are_not_a_breach(tmp_path):
    r = _run(tmp_path, [_case("ok", 1.0), _case("s1", 0.0, outcome="skipped")], 5.0)
    assert r.returncode == 0, _out(r)


def test_a_skip_breach_repro_command_carries_the_ceiling(tmp_path):
    r = _run(tmp_path, [_case("ok", 1.0), _case("s1", 0.0, outcome="skipped")], 5.0, "--max-skipped", "0")
    assert "--max-skipped 0" in r.stderr


def test_summary_is_written_and_never_contains_captured_output(tmp_path):
    cases = [_case("t", 1.0, out=MARKER), _case("u", 2.0, outcome="failed", out=MARKER)]
    summary = tmp_path / "summary.md"
    r = _run(tmp_path, cases, 5.0, "--summary", str(summary), "--budget", "1")
    assert r.returncode == 0, _out(r)
    text = summary.read_text(encoding="utf-8")
    assert "mod::u" in text and "| trailhead/tests | 3.0 |" in text
    assert MARKER not in text
    assert MARKER not in _out(r)


def test_truncated_xml_gives_a_clean_error_naming_the_file(tmp_path):
    full = _xml([_case("t", 1.0), _case("u", 2.0)], 5.0)
    xml = tmp_path / "crashed.xml"
    xml.write_text(full[: len(full) // 2], encoding="utf-8")
    r = _run_file(xml)
    assert r.returncode == 2
    assert r.stderr.startswith("suite-time-report: cannot parse ")
    assert str(xml) in r.stderr
    assert "Traceback" not in r.stderr


def test_malformed_xml_gives_a_clean_error(tmp_path):
    xml = tmp_path / "bad.xml"
    xml.write_text("this is not xml", encoding="utf-8")
    r = _run_file(xml)
    assert r.returncode == 2
    assert r.stderr.startswith("suite-time-report: ") and "Traceback" not in r.stderr


def test_missing_xml_gives_a_clean_error(tmp_path):
    xml = tmp_path / "nope.xml"
    r = _run_file(xml)
    assert r.returncode == 2
    assert r.stderr.startswith("suite-time-report: cannot read ") and str(xml) in r.stderr
    assert "Traceback" not in r.stderr


def test_xml_without_a_testsuite_is_rejected_cleanly(tmp_path):
    xml = tmp_path / "empty.xml"
    xml.write_text("<testsuites/>", encoding="utf-8")
    r = _run_file(xml)
    assert r.returncode == 2 and "no <testsuite>" in r.stderr


def test_a_real_pytest_run_is_measured_end_to_end(tmp_path):  # inert-gate: allow seam smoke over a real xdist run's XML
    (tmp_path / "test_real.py").write_text(
        "import pytest\n"
        "def test_a():\n    assert True\n"
        "def test_b():\n    assert True\n"
        "@pytest.mark.skip\ndef test_c():\n    pass\n",
        encoding="utf-8",
    )
    xml = tmp_path / "real.xml"
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-n", "2", "-o", "addopts=",
         "--junitxml", str(xml), "-o", "junit_family=xunit1", "-o", "junit_duration_report=total",
         "-q", str(tmp_path / "test_real.py")],
        capture_output=True, text=True, timeout=120, cwd=tmp_path,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    r = _run_file(xml, "--budget", "600", area=("test_real.py",))
    assert r.returncode == 0, _out(r)
    assert "collected 3, passed 2, failed 0, skipped 1" in r.stdout
