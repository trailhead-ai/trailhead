"""scripts/eval-arm-compare run against result JSONs written to a temp dir."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

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
        _doc({"a": s}),
        _doc({"a": f}),
        _doc({"a": n}),
        ["AC61=a"],
    )


def test_slimmed_above_frozen_above_none_passes(tmp_path):
    r = _one(_run(3), _run(2), _run(0), tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "a" in r.stdout and "pass" in r.stdout
    assert "3/3" in r.stdout and "2/3" in r.stdout and "0/3" in r.stdout


def test_slimmed_below_frozen_fails_naming_scenario(tmp_path):
    r = _one(_run(1), _run(2), _run(0), tmp_path=tmp_path)
    assert r.returncode == 1
    assert "a" in (r.stdout + r.stderr) and "fail" in r.stdout


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
        _doc({"a": _run(3), "b": _run(3)}),
        _doc({"a": _run(2), "b": _run(3)}),
        _doc({"a": _run(0), "b": _run(3)}),
        ["AC61=a,b"],
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "inconclusive" in r.stdout


def test_failing_scenario_named_and_passing_one_not_in_failure_message(tmp_path):
    r = _go(
        tmp_path,
        _doc({"good": _run(3), "bad": _run(0)}),
        _doc({"good": _run(2), "bad": _run(2)}),
        _doc({"good": _run(0), "bad": _run(0)}),
        ["AC61=good,bad"],
    )
    assert r.returncode == 1
    assert "bad" in r.stderr
    assert "good" not in r.stderr


def test_errored_run_with_passed_true_is_not_counted_and_is_listed(tmp_path):
    slim = _run(4, total=4)
    slim[0]["error"] = "timeout"
    r = _go(tmp_path, _doc({"a": slim}), _doc({"a": _run(2)}), _doc({"a": _run(0)}), ["AC61=a"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "slimmed=3/3" in r.stdout
    assert "timeout" in r.stdout


def test_errored_run_counts_as_error_not_pass_changes_verdict(tmp_path):
    slim = _run(3)
    slim[0]["error"] = "timeout"
    slim.append(_run(0, total=1)[0])
    r = _go(tmp_path, _doc({"a": slim}), _doc({"a": _run(3)}), _doc({"a": _run(0)}), ["AC61=a"])
    assert r.returncode == 1
    assert "fail" in r.stdout
    assert "timeout" in r.stdout


def test_scenario_missing_from_an_arm_fails_naming_arm(tmp_path):
    r = _go(
        tmp_path,
        _doc({"a": _run(3)}),
        _doc({"a": _run(2), "b": _run(2)}),
        _doc({"a": _run(0), "b": _run(0)}),
        ["AC61=a,b"],
    )
    assert r.returncode == 1
    assert "slimmed" in r.stdout + r.stderr and "b" in r.stdout + r.stderr


def test_under_sampled_arm_fails(tmp_path):
    r = _one(_run(2, total=2), _run(2), _run(0), tmp_path=tmp_path)
    assert r.returncode == 1
    out = r.stdout + r.stderr
    assert "slimmed" in out and "under-sampled" in out


def test_malformed_json_exits_2_without_traceback(tmp_path):
    r = _go(tmp_path, _doc({"a": _run(3)}), _doc({"a": _run(2)}), _doc({"a": _run(0)}),
            ["AC61=a"], raw={"frozen": "{not json"})
    assert r.returncode == 2
    assert "frozen" in r.stderr and "Traceback" not in r.stderr


def test_missing_file_exits_2_without_traceback(tmp_path):
    r = subprocess.run(
        [sys.executable, str(_SCRIPT), "--slimmed", str(tmp_path / "x"), "--frozen",
         str(tmp_path / "y"), "--none", str(tmp_path / "z"), "--family", "AC61=a"],
        capture_output=True, text=True,
    )
    assert r.returncode == 2
    assert "Traceback" not in r.stderr and "x" in r.stderr


def test_family_case_no_arm_ran_exits_2(tmp_path):
    r = _go(tmp_path, _doc({"a": _run(3)}), _doc({"a": _run(2)}), _doc({"a": _run(0)}),
            ["AC61=a,ghost"])
    assert r.returncode == 2
    assert "ghost" in r.stderr and "Traceback" not in r.stderr
