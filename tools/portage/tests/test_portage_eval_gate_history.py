"""The eval gate's timing history — every case a runner started on is appended to history.jsonl.

Tests drive ``dispatch.main(["eval-gate", "run", ...])`` (or ``run_case`` with an injected
``clock`` and ``now``) against the fake runner of the run tests, on a real plugin tree under
``tmp_path``. The cache directory is pointed under ``tmp_path`` so no test reaches the real
``~/.cache``. The clock only moves when a test's runner wrapper moves it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from _portage_cli import contained_tempdir  # noqa: F401  (autouse fixture)

from portage.eval_gate import history
from portage.eval_gate import run as gate_run
from portage.eval_gate.run import run_case
from test_portage_eval_gate_run import ERR, FAIL, PASS, Env, _result

NOW = datetime(2026, 11, 19, 12, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    e.cache = tmp_path / "cache"
    monkeypatch.setenv("PORTAGE_CACHE_DIR", str(e.cache))
    monkeypatch.delenv("PORTAGE_EVAL_RUNNER", raising=False)
    plugin = e.repo / "plugins" / "p"
    for rel in ("evals/c1/case.yaml", "evals/c2/case.yaml"):
        (plugin / rel).parent.mkdir(parents=True, exist_ok=True)
        (plugin / rel).write_text("name: x\n")
    e.cases = [dict(e.cases[0], case="c1"), dict(e.cases[0], case="c2")]
    e.plan(tier="merge")
    e.fake(pool=_result([PASS] * 12, costUsd=0.1), code=0)
    yield e
    shutil.rmtree(e.root, ignore_errors=True)


def _lines(env, name="history.jsonl") -> list[dict]:
    f = env.cache / "eval-gate" / name
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


class _Clock:
    """A clock the runner wrapper moves: the canary takes 100 s, a case call 7 s."""

    def __init__(self, mp):
        self.t = 1000.0
        real = gate_run._execute

        def execute(runner, work_dir, timeout):
            self.t += 100.0 if "--eval-dir" in runner.argv else 7.0
            return real(runner, work_dir, timeout)

        mp.setattr(gate_run, "_execute", execute)

    def __call__(self):
        return self.t


def _run(env, case="c1", clock=None, **kw):
    return run_case(env.state, case, clock=clock or (lambda: 0.0), now=lambda: NOW,
                    env=dict(os.environ), **kw)


class TestAppendedLine:
    def test_two_cases_append_two_lines_in_order_with_every_field(self, env):
        clock = _Clock(env.mp)
        _run(env, "c1", clock)
        _run(env, "c2", clock, timeout_seconds=300)
        first, second = _lines(env)
        assert [first["case"], second["case"]] == ["c1", "c2"]
        assert first["recorded_at"] == "2026-11-19T12:30:00Z"
        assert first["plugin"] == "p"
        assert re.fullmatch(r"[0-9a-f]{64}", first["fingerprint"])
        assert first["fingerprint"] != second["fingerprint"]
        assert first["runner_version"] == "2.1.306"
        assert first["pinned_runner"] is False
        assert first["tier"] == "merge"
        assert (first["verdict"], first["reason"]) == ("pass", None)
        assert (first["budget_seconds"], second["budget_seconds"]) == (540, 300)
        assert len(first["runs"]) == 2
        assert set(first["runs"][0]) == {"passed", "errored", "duration_seconds", "cost_usd",
                                         "judge_cost_usd"}
        assert first["runs"][0]["passed"] is True

    def test_the_first_cases_elapsed_excludes_the_canary(self, env):
        _run(env, "c1", _Clock(env.mp))
        assert _lines(env)[0]["elapsed_seconds"] == 7.0

    def test_the_second_case_pays_no_canary(self, env):
        clock = _Clock(env.mp)
        _run(env, "c1", clock)
        _run(env, "c2", clock)
        assert _lines(env)[1]["elapsed_seconds"] == 7.0

    def test_a_scored_fail_is_written(self, env):
        env.fake(pool=_result([FAIL] * 3, costUsd=0.1), code=1)
        _run(env)
        assert (_lines(env)[0]["verdict"], _lines(env)[0]["reason"]) == ("fail", None)

    def test_a_not_run_reached_after_the_runner_started_is_written(self, env):
        env.fake(pool=_result([PASS, FAIL, ERR, ERR, ERR], costUsd=0.1), code=1)
        _run(env)
        line = _lines(env)[0]
        assert (line["verdict"], line["reason"]) == ("not-run", "runs-errored")
        assert [r["errored"] for r in line["runs"]][:3] == [False, False, True]

    def test_a_case_killed_at_the_budget_is_written_with_null_durations(self, env):
        env.fake(sleep=60)
        assert env.run("c1", "--timeout-seconds", "2") == 0
        line = _lines(env)[0]
        assert (line["verdict"], line["reason"]) == ("not-run", "timeout")
        assert line["runs"] and all(r["duration_seconds"] is None for r in line["runs"])

    def test_the_pinned_runner_is_recorded(self, env):
        pinned = env.fake(pool=_result([PASS] * 12, costUsd=0.1), path=env.tmp / "pinned")
        env.mp.setenv("PORTAGE_EVAL_RUNNER", str(pinned))
        assert env.run() == 0
        line = _lines(env)[0]
        assert line["pinned_runner"] is True
        assert re.fullmatch(r"[0-9a-f]{64}", line["fingerprint"])

    @pytest.mark.parametrize("version,expected", [
        ("2.1.306", "2.1.306"), ("1.0.0-rc.1+b5", "1.0.0-rc.1+b5"),
        ("$(touch x)", None), ("a" * 33, None), ("v2", None), (None, None), (7, None)])
    def test_the_runner_version_is_kept_only_when_it_has_the_shape_of_one(
            self, env, version, expected):
        env.fake(pool=_result([PASS] * 12, costUsd=0.1, claudeVersion=version), code=0)
        _run(env)
        assert _lines(env)[0]["runner_version"] == expected

    def test_an_uncacheable_case_has_a_null_fingerprint(self, env):
        (env.repo / "plugins" / "p" / "evals" / "c1" / "link").symlink_to("case.yaml")
        env.plan(tier="merge")
        _run(env)
        assert _lines(env)[0]["fingerprint"] is None


class TestNoLine:
    def test_a_cache_hit_appends_nothing(self, env):
        assert env.run() == 0
        assert len(_lines(env)) == 1
        assert env.run() == 0
        assert env.result()["cached"] is True
        assert len(_lines(env)) == 1

    @pytest.mark.parametrize("decided", [
        "case-removed-on-branch", "corpus-new-on-branch", "head-moved", "no-harness",
        "failed-canary", "canary-not-run", "path-too-long"])
    def test_a_not_run_decided_before_any_runner_started_appends_nothing(self, env, decided):
        kw = {}
        if decided == "case-removed-on-branch":
            env.cases[0]["case_removed"] = True
            env.plan()
        elif decided == "corpus-new-on-branch":
            env.cases[0]["new_runner_grants"] = True
            env.plan()
        elif decided == "head-moved":
            (env.repo / "plugins" / "p" / "evals" / "c1" / "case.yaml").write_text("changed\n")
        elif decided == "no-harness":
            kw["harnesses"] = []
        elif decided == "failed-canary":
            env.fake(pool=_result([PASS] * 12), canary=_result([FAIL]), code=1)
        elif decided == "canary-not-run":
            env.fake(pool=_result([PASS] * 12), canary="garbage", code=1)
        elif decided == "path-too-long":
            env.mp.setenv("XDG_CACHE_HOME", str(env.root / ("x" * 60)))
            (env.root / ("x" * 60)).mkdir()
        _run(env, **kw)
        assert (env.state / "results" / "c1.json").exists()
        assert _lines(env) == []
        assert not (env.cache / "eval-gate" / "history.jsonl").exists()


class TestRunnerLaunchFailure:
    @pytest.mark.parametrize("fails_on_call", [1, 2])
    def test_a_runner_that_cannot_be_launched_appends_nothing(self, env, fails_on_call):
        env.fake(pool=_result([PASS, FAIL, PASS], costUsd=0.1), code=1)
        real, calls = gate_run._execute, []

        def execute(runner, work_dir, timeout):
            if "--eval-dir" in runner.argv:
                return real(runner, work_dir, timeout)
            calls.append(1)
            if len(calls) == fails_on_call:
                return None, False, True
            return real(runner, work_dir, timeout)

        env.mp.setattr(gate_run, "_execute", execute)
        _run(env)
        assert len(calls) == fails_on_call
        assert (env.state / "results" / "c1.json").exists()
        assert env.result()["reason"] == "runner-missing"
        assert _lines(env) == []


class TestCannotBeWritten:
    def _control(self, env):
        assert env.run() == 0
        shutil.rmtree(env.cache)
        return env.result(), (env.state / "results" / "c1.json").read_bytes()

    def _broken(self, env, capsys, how, tmp_path):
        before = self._control(env)
        eg = env.cache / "eval-gate"
        eg.mkdir(parents=True, mode=0o700)
        outside = tmp_path / "outside.jsonl"
        if how == "group-writable":
            os.chmod(eg, 0o770)
        elif how == "directory":
            (eg / "history.jsonl").mkdir()
        elif how == "symlink":
            (eg / "history.jsonl").symlink_to(outside)
        elif how in ("fifo", "fifo-with-reader"):
            os.mkfifo(eg / "history.jsonl")
            if how == "fifo-with-reader":
                self.reader = os.open(eg / "history.jsonl", os.O_RDONLY | os.O_NONBLOCK)
        capsys.readouterr()
        try:
            assert env.run() == 0
        finally:
            if getattr(self, "reader", None) is not None:
                os.close(self.reader)
        err = capsys.readouterr().err
        return before, err, outside

    @pytest.mark.parametrize("how", ["group-writable", "directory", "symlink", "fifo", "fifo-with-reader"])
    def test_the_result_and_exit_code_are_as_without_the_history(self, env, capsys, tmp_path, how):
        (result, raw), err, outside = self._broken(env, capsys, how, tmp_path)
        assert env.result() == result
        assert (env.state / "results" / "c1.json").read_bytes() == raw

    @pytest.mark.parametrize("how", ["group-writable", "directory", "symlink", "fifo", "fifo-with-reader"])
    def test_stderr_carries_the_one_line(self, env, capsys, tmp_path, how):
        _, err, _ = self._broken(env, capsys, how, tmp_path)
        lines = [x for x in err.splitlines() if x.startswith("eval-gate: timing history")]
        assert len(lines) == 1 and lines[0].startswith("eval-gate: timing history not written: ")

    def test_nothing_is_written_through_a_symlink(self, env, capsys, tmp_path):
        _, _, outside = self._broken(env, capsys, "symlink", tmp_path)
        assert not outside.exists()

    def test_a_short_write_is_a_warning_not_a_silent_torn_line(self, tmp_path, capsys):
        env = {"PORTAGE_CACHE_DIR": str(tmp_path / "cache")}
        history.append({"a": 1}, env, write=lambda fd, b: 3)
        err = capsys.readouterr().err
        assert err.startswith("eval-gate: timing history not written: ") and err.count("\n") == 1

    def test_a_full_write_is_not_a_warning(self, tmp_path, capsys):
        env = {"PORTAGE_CACHE_DIR": str(tmp_path / "cache")}
        history.append({"a": 1}, env)
        assert capsys.readouterr().err == ""
        assert json.loads(history.history_path(env).read_text()) == {"a": 1}

    def test_a_healthy_history_prints_nothing_to_stderr(self, env, capsys):
        assert env.run() == 0
        assert "timing history" not in capsys.readouterr().err


class TestRotationAndConcurrency:
    def _seed(self, env, size):
        d = env.cache / "eval-gate"
        d.mkdir(parents=True, mode=0o700)
        with open(d / "history.jsonl", "wb") as f:
            f.truncate(size)

    def test_a_history_just_over_the_limit_is_rotated_before_the_append(self, env):
        self._seed(env, history.MAX_BYTES + 1)
        _run(env)
        assert (env.cache / "eval-gate" / "history.1.jsonl").stat().st_size == history.MAX_BYTES + 1
        assert [x["case"] for x in _lines(env)] == ["c1"]

    def test_a_history_at_the_limit_is_appended_to_not_rotated(self, env):
        self._seed(env, history.MAX_BYTES)
        _run(env)
        eg = env.cache / "eval-gate"
        assert not (eg / "history.1.jsonl").exists()
        assert (eg / "history.jsonl").stat().st_size > history.MAX_BYTES

    def test_rotation_replaces_an_earlier_rotated_file(self, env):
        self._seed(env, history.MAX_BYTES + 1)
        (env.cache / "eval-gate" / "history.1.jsonl").write_text("old\n")
        _run(env)
        assert (env.cache / "eval-gate" / "history.1.jsonl").stat().st_size == history.MAX_BYTES + 1

    def test_two_processes_appending_200_lines_each_leave_400_parseable_lines(self, env):
        code = (
            "import sys; from datetime import datetime, timezone\n"
            "from portage.eval_gate import history\n"
            "rec = {'case': sys.argv[2], 'pad': 'x' * 3000}\n"
            "for i in range(200):\n"
            "    history.append(rec | {'i': i}, {'PORTAGE_CACHE_DIR': sys.argv[1]})\n")
        procs = [subprocess.Popen([sys.executable, "-c", code, str(env.cache), name],
                                  env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
                 for name in ("a", "b")]
        assert [p.wait() for p in procs] == [0, 0]
        lines = _lines(env)
        assert len(lines) == 400
        assert sorted({x["case"] for x in lines}) == ["a", "b"]

    def test_the_file_is_private(self, env):
        _run(env)
        assert (env.cache / "eval-gate" / "history.jsonl").stat().st_mode & 0o777 == 0o600
