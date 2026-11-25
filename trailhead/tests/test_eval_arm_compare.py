"""scripts/eval-arm-compare run against result JSONs written to a temp dir."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval-arm-compare"


def _run(passed: int, total: int = 3, errors: int = 0) -> list[dict]:
    runs = []
    for i in range(total):
        ok = i < passed
        err = "boom" if i >= total - errors else None
        runs.append({"score": 1 if ok else 0, "passed": ok, "error": err, "graders": []})
    return runs


def _doc(cases: dict[str, list[dict]]) -> dict:
    return {
        "schemaVersion": 1,
        "partial": False,
        "cases": [{"name": n, "arms": {"with": runs}} for n, runs in cases.items()],
    }


def _go(tmp_path: Path, slimmed, frozen, none, families, raw: dict[str, str] | None = None):
    paths = {}
    for label, doc in (("slimmed", slimmed), ("frozen", frozen), ("none", none)):
        p = tmp_path / f"{label}.json"
        p.write_text(json.dumps(doc))
        paths[label] = p
    for label, text in (raw or {}).items():
        paths[label].write_text(text)
    args = [sys.executable, str(_SCRIPT)]
    for label, p in paths.items():
        args += [f"--{label}", str(p)]
    for fam in families:
        args += ["--family", fam]
    return subprocess.run(args, capture_output=True, text=True)


def _one(s, f, n, **kw):
    return _go(
        kw.pop("tmp_path"),
        _doc({"exporter-case": s}),
        _doc({"exporter-case": f}),
        _doc({"exporter-case": n}),
        ["AC61=exporter-case"],
    )


def test_slimmed_above_frozen_above_none_passes(tmp_path):
    r = _one(_run(3), _run(2), _run(0), tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "exporter-case: slimmed=3/3 frozen=2/3 none=0/3 verdict=pass" in r.stdout


def test_slimmed_below_frozen_fails_naming_scenario(tmp_path):
    r = _one(_run(1), _run(2), _run(0), tmp_path=tmp_path)
    assert r.returncode == 1
    assert "FAIL" in r.stderr and "exporter-case" in r.stderr
    assert "exporter-case" in r.stdout and "verdict=fail" in r.stdout


def test_slimmed_equal_to_frozen_passes(tmp_path):
    r = _one(_run(2), _run(2), _run(0), tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_frozen_equal_to_none_is_inconclusive_and_family_fails(tmp_path):
    r = _one(_run(3), _run(3), _run(3), tmp_path=tmp_path)
    assert "inconclusive" in r.stdout
    assert r.returncode == 1
    assert "AC61" in r.stdout + r.stderr


def test_family_with_one_discriminating_scenario_passes(tmp_path):
    r = _go(
        tmp_path,
        _doc({"exporter-case": _run(3), "retry-case": _run(3)}),
        _doc({"exporter-case": _run(2), "retry-case": _run(3)}),
        _doc({"exporter-case": _run(0), "retry-case": _run(3)}),
        ["AC61=exporter-case,retry-case"],
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "inconclusive" in r.stdout


def test_failing_scenario_named_and_passing_one_not_in_failure_message(tmp_path):
    r = _go(
        tmp_path,
        _doc({"good-case": _run(3), "bad-case": _run(0)}),
        _doc({"good-case": _run(2), "bad-case": _run(2)}),
        _doc({"good-case": _run(0), "bad-case": _run(0)}),
        ["AC61=good-case,bad-case"],
    )
    assert r.returncode == 1
    assert "bad-case" in r.stderr
    assert "good-case" not in r.stderr


def test_errored_run_with_passed_true_is_not_counted_and_is_listed(tmp_path):
    slim = _run(4, total=4)
    slim[0]["error"] = "timeout"
    r = _go(tmp_path, _doc({"exporter-case": slim}), _doc({"exporter-case": _run(2)}), _doc({"exporter-case": _run(0)}), ["AC61=exporter-case"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "slimmed=3/3" in r.stdout
    assert "timeout" in r.stdout


def test_errored_run_counts_as_error_not_pass_changes_verdict(tmp_path):
    slim = _run(3)
    slim[0]["error"] = "timeout"
    slim.append(_run(0, total=1)[0])
    r = _go(tmp_path, _doc({"exporter-case": slim}), _doc({"exporter-case": _run(3)}), _doc({"exporter-case": _run(0)}), ["AC61=exporter-case"])
    assert r.returncode == 1
    assert "fail" in r.stdout
    assert "timeout" in r.stdout


def test_scenario_missing_from_an_arm_fails_naming_arm(tmp_path):
    r = _go(
        tmp_path,
        _doc({"exporter-case": _run(3)}),
        _doc({"exporter-case": _run(2), "retry-case": _run(2)}),
        _doc({"exporter-case": _run(0), "retry-case": _run(0)}),
        ["AC61=exporter-case,retry-case"],
    )
    assert r.returncode == 1
    assert "FAIL" in r.stderr and "slimmed" in r.stderr and "retry-case" in r.stderr


def test_under_sampled_arm_fails(tmp_path):
    r = _one(_run(2, total=2), _run(2), _run(0), tmp_path=tmp_path)
    assert r.returncode == 1
    out = r.stdout + r.stderr
    assert "slimmed" in out and "under-sampled" in out


def test_malformed_json_exits_2_without_traceback(tmp_path):
    r = _go(tmp_path, _doc({"exporter-case": _run(3)}), _doc({"exporter-case": _run(2)}), _doc({"exporter-case": _run(0)}),
            ["AC61=exporter-case"], raw={"frozen": "{not json"})
    assert r.returncode == 2
    assert "frozen" in r.stderr and "Traceback" not in r.stderr


def test_missing_file_exits_2_without_traceback(tmp_path):
    r = subprocess.run(
        [sys.executable, str(_SCRIPT), "--slimmed", str(tmp_path / "x"), "--frozen",
         str(tmp_path / "y"), "--none", str(tmp_path / "z"), "--family", "AC61=exporter-case"],
        capture_output=True, text=True,
    )
    assert r.returncode == 2
    assert "Traceback" not in r.stderr and str(tmp_path / "x") in r.stderr


def test_family_case_no_arm_ran_exits_2(tmp_path):
    r = _go(tmp_path, _doc({"exporter-case": _run(3)}), _doc({"exporter-case": _run(2)}), _doc({"exporter-case": _run(0)}),
            ["AC61=exporter-case,ghost"])
    assert r.returncode == 2
    assert "ghost" in r.stderr and "Traceback" not in r.stderr


def test_duplicate_family_name_exits_2(tmp_path):
    doc = lambda n: _doc({"a": _run(n), "b": _run(n)})
    r = _go(tmp_path, doc(3), doc(2), doc(0), ["AC61=a", "AC61=b"])
    assert r.returncode == 2
    assert "AC61" in r.stderr and "duplicate" in r.stderr and "Traceback" not in r.stderr


def test_skipped_paid_graders_run_is_not_a_pass_and_is_listed(tmp_path):
    slim = _run(4, total=4)
    slim[0]["skippedPaidGraders"] = True
    r = _go(tmp_path, _doc({"exporter-case": slim}), _doc({"exporter-case": _run(2)}),
            _doc({"exporter-case": _run(0)}), ["AC61=exporter-case"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "slimmed=3/3" in r.stdout
    assert "skippedPaidGraders" in r.stdout


@pytest.mark.parametrize("s, f, n", [
    (_run(3, total=4), _run(3), _run(0)),
    (_run(3), _run(3, total=4), _run(0)),
    (_run(3), _run(2), _run(0, total=4)),
])
def test_arms_with_different_graded_totals_fail_naming_the_scenario(s, f, n, tmp_path):
    r = _one(s, f, n, tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "exporter-case" in r.stderr and "graded" in r.stderr
    assert "verdict=fail" in r.stdout


def test_a_run_entry_that_is_not_an_object_is_a_usage_error_without_a_traceback(tmp_path):
    bad = _doc({"exporter-case": _run(3)})
    bad["cases"][0]["arms"]["with"][1] = "not-a-dict"
    r = _go(tmp_path, bad, _doc({"exporter-case": _run(2)}), _doc({"exporter-case": _run(0)}),
            ["AC61=exporter-case"])
    assert r.returncode == 2
    assert "Traceback" not in r.stderr
    assert "eval-arm-compare:" in r.stderr


def test_a_duplicate_case_name_in_one_arm_is_a_usage_error_naming_it(tmp_path):
    dup = _doc({"exporter-case": _run(3)})
    dup["cases"].append({"name": "exporter-case", "arms": {"with": _run(0)}})
    r = _go(tmp_path, dup, _doc({"exporter-case": _run(2)}), _doc({"exporter-case": _run(0)}),
            ["AC61=exporter-case"])
    assert r.returncode == 2
    assert "exporter-case" in r.stderr and "duplicate" in r.stderr
    assert "Traceback" not in r.stderr


def test_control_characters_in_a_case_name_cannot_forge_a_line_or_emit_escapes(tmp_path):
    evil = "x\nFAIL: forged\x1b[31m"
    r = _go(tmp_path, _doc({evil: _run(1)}), _doc({evil: _run(2)}), _doc({evil: _run(0)}), [])
    assert r.returncode == 1
    for stream in (r.stdout, r.stderr):
        assert "\x1b" not in stream
        assert not any(line.startswith("FAIL: forged") for line in stream.splitlines())
    assert "\\x0a" in r.stdout and "\\x1b" in r.stdout


def test_control_characters_in_a_run_error_cannot_forge_a_line_or_emit_escapes(tmp_path):
    runs = _run(3)
    runs[0]["error"] = "boom\nFAIL: forged\x1b[2J"
    r = _go(tmp_path, _doc({"c": runs}), _doc({"c": _run(2)}), _doc({"c": _run(0)}), [])
    for stream in (r.stdout, r.stderr):
        assert "\x1b" not in stream
        assert not any(line.startswith("FAIL: forged") for line in stream.splitlines())
    assert "\\x1b" in r.stdout
