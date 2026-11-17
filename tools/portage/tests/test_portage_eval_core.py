"""The eval core: batched deciding, the temp-dir rules and the injected executor, driven through
the same fake ``claude`` runner the gate's tests use; and the gate's result files staying
score-free and within the gate's reason vocabulary."""

from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from _portage_cli import contained_tempdir  # noqa: F401  (autouse fixture)

from portage.eval_core import TmpBaseRefused, TmpPathTooLong, decide_case, execute, run_canary
from test_portage_eval_gate_run import ERR, FAIL, PASS, RUNNER, Env, _result

HALF = dict(FAIL, score=0.5)


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    e.plan()
    yield e
    shutil.rmtree(e.root, ignore_errors=True)


def _no_override(_env):
    return None


def _no_pin(runner, _override):
    return runner


def _call(e, runs=3, pass_at=2, **over):
    kw = dict(
        plugin_dir=e.repo / "plugins" / "p", case="c1", runs=runs, pass_at=pass_at,
        runner=RUNNER, output_dir=e.state / "runs", timeout_seconds=60, work_dir=e.state,
        execute=execute, resolve_runner=_no_override, pin=_no_pin, env=dict(os.environ))
    kw.update(over)
    return decide_case(**kw)


def _asked(env):
    return [int(env.asked(c, "--runs")) for c in env.case_calls]


class TestBatchedDeciding:
    def test_two_full_marks_of_three_pass_after_one_batch_and_report_scores(self, env):
        env.fake(pool=_result([PASS, PASS, PASS], costUsd=0.1))
        result = _call(env)
        assert (result["verdict"], result["reason"]) == ("pass", None)
        assert _asked(env) == [2]
        assert [r["score"] for r in result["runs"]] == [1, 1]

    def test_a_half_score_needs_a_second_batch_and_the_pass_comes_after_it(self, env):
        env.fake(pool=_result([PASS, HALF, PASS], costUsd=0.1))
        result = _call(env)
        assert (result["verdict"], result["passed"], result["failed"]) == ("pass", 2, 1)
        assert _asked(env) == [2, 1]
        assert [r["score"] for r in result["runs"]] == [1, 0.5, 1]

    def test_two_half_scores_fail_after_the_first_batch(self, env):
        env.fake(pool=_result([HALF, HALF, PASS], costUsd=0.1))
        result = _call(env)
        assert (result["verdict"], result["failed"]) == ("fail", 2)
        assert _asked(env) == [2]
        assert [r["score"] for r in result["runs"]] == [0.5, 0.5]

    def test_each_run_carries_its_graders_names_and_pass_flags(self, env):
        env.fake(pool=_result([PASS, HALF, ERR], costUsd=0.1))
        run = _call(env)["runs"][1]
        assert run["graders"] == [{"name": "no-change-history", "passed": True},
                                  {"name": "original-figure-kept", "passed": True},
                                  {"name": "task-created", "passed": False},
                                  {"name": "task-names-record-and-decider", "passed": False}]

    def test_five_runs_with_pass_at_three_decide_on_that_bar(self, env):
        env.fake(pool=_result([PASS] * 5))
        result = _call(env, runs=5, pass_at=3)
        assert (result["verdict"], result["passed"]) == ("pass", 3)
        assert _asked(env) == [3]

    def test_five_runs_with_pass_at_three_fail_only_once_three_have_failed(self, env):
        env.fake(pool=_result([PASS, PASS, HALF, HALF, HALF], costUsd=0.1))
        result = _call(env, runs=5, pass_at=3)
        assert (result["verdict"], result["passed"], result["failed"]) == ("fail", 2, 3)
        assert _asked(env) == [3, 1, 1]


class TestInjection:
    def test_the_injected_executor_runs_the_canary_and_every_batch(self, env):
        env.fake(pool=_result([PASS, HALF, PASS], costUsd=0.1))
        seen = []

        def counting(runner, cwd, timeout):
            seen.append(Path(cwd))
            return execute(runner, cwd, timeout)

        _call(env, execute=counting)
        assert seen == [env.state] * 3

    def test_the_injected_resolution_pins_the_runner_binary(self, env):
        env.fake(pool=_result([PASS, PASS, PASS], costUsd=0.1))
        other = env.bin / "other-claude"
        shutil.copy(env.bin / "claude", other)
        other.chmod(0o755)
        _call(env, resolve_runner=lambda _env: str(other),
              pin=lambda r, o: type(r)(argv=[o, *r.argv[1:]], env=r.env))
        assert env.calls and all(c["argv"][0] == str(other) for c in env.calls)

    def test_a_resolution_that_refuses_starts_nothing_and_is_not_run_runner_missing(self, env):
        env.fake(pool=_result([PASS, PASS, PASS], costUsd=0.1))
        result = _call(env, resolve_runner=lambda _env: False)
        assert (result["verdict"], result["reason"]) == ("not-run", "runner-missing")
        assert not env.started


class TestStrictTmp:
    def _under_tmp(self, env):
        return {**os.environ, "XDG_CACHE_HOME": str(env.root / "c")}

    def test_a_cache_base_under_tmp_refuses_before_any_runner_starts(self, env):
        env.fake(pool=_result([PASS, PASS, PASS], costUsd=0.1))
        with pytest.raises(TmpBaseRefused):
            _call(env, strict_tmp=True, env=self._under_tmp(env))
        assert not env.started

    def test_the_canary_is_refused_too_under_a_tmp_base(self, env):
        env.fake()
        with pytest.raises(TmpBaseRefused):
            run_canary(work_dir=env.state, timeout_seconds=60, strict_tmp=True, execute=execute,
                       resolve_runner=_no_override, pin=_no_pin, env=self._under_tmp(env))
        assert not env.started

    def test_a_refused_base_never_reaches_the_canary_step(self, env):
        env.fake()
        called = []
        with pytest.raises(TmpBaseRefused):
            _call(env, strict_tmp=True, env=self._under_tmp(env),
                  canary=lambda run, timeout: called.append(1) or run(timeout))
        assert not called and not env.started

    def test_without_strict_tmp_a_tmp_base_runs(self, env):
        env.fake(pool=_result([PASS, PASS, PASS], costUsd=0.1))
        result = _call(env, env=self._under_tmp(env))
        assert result["verdict"] == "pass"

    def test_a_per_call_path_over_51_characters_refuses_before_any_runner_starts(self, env):
        env.fake(pool=_result([PASS, PASS, PASS], costUsd=0.1))
        long_base = {**os.environ, "XDG_CACHE_HOME": str(env.root / ("x" * 30))}
        with pytest.raises(TmpPathTooLong):
            _call(env, env=long_base)
        assert not env.started

    def test_the_canary_is_refused_on_a_path_over_51_characters(self, env):
        env.fake()
        long_base = {**os.environ, "XDG_CACHE_HOME": str(env.root / ("x" * 30))}
        with pytest.raises(TmpPathTooLong):
            run_canary(work_dir=env.state, timeout_seconds=60, execute=execute,
                       resolve_runner=_no_override, pin=_no_pin, env=long_base)
        assert not env.started


class TestGateTempDirOrder:
    def _long_cache(self, env):
        cache = env.root / ("c" * 40)
        env.mp.setenv("XDG_CACHE_HOME", str(cache))
        return cache

    def test_a_group_writable_base_that_is_also_too_long_is_a_runner_error(self, env):
        base = self._long_cache(env) / "pegt"
        base.mkdir(parents=True, mode=0o700)
        base.chmod(0o770)
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        r = env.result()
        assert (r["verdict"], r["reason"]) == ("not-run", "runner-error")
        assert not env.started

    def test_a_symlinked_base_that_is_also_too_long_is_a_runner_error(self, env):
        base = self._long_cache(env) / "pegt"
        base.parent.mkdir(parents=True)
        real = env.root / "real"
        real.mkdir(mode=0o700)
        base.symlink_to(real)
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        r = env.result()
        assert (r["verdict"], r["reason"]) == ("not-run", "runner-error")
        assert not env.started

    def test_a_valid_base_that_is_too_long_is_still_sandbox_unwritable(self, env):
        self._long_cache(env)
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        r = env.result()
        assert (r["verdict"], r["reason"]) == ("not-run", "runner-sandbox-unwritable")
        assert not env.started


class TestGateResultFiles:
    def test_a_gate_run_writes_no_score_into_the_result_file(self, env):
        env.fake(pool=_result([PASS, HALF, PASS], costUsd=0.1))
        assert env.run() == 0
        runs = env.result()["runs"]
        assert len(runs) == 3
        assert all(set(r) == {"passed", "errored", "graders"} for r in runs)

    def test_the_cached_canary_carries_no_score_either(self, env):
        env.fake(pool=_result([PASS, PASS, PASS], costUsd=0.1))
        env.run()
        canary = json.loads((env.state / "canary.json").read_text())
        assert canary["runs"] and all("score" not in r for r in canary["runs"])

    @pytest.mark.parametrize("name,kw", [
        ("pass", dict(pool=_result([PASS] * 3))),
        ("fail", dict(pool=_result([HALF] * 3), code=1)),
        ("errored", dict(pool=_result([PASS, ERR, ERR]), code=1)),
        ("garbage", dict(doc="garbage", code=1)),
        ("timeout", dict(pool=_result([PASS]), sleep=30)),
        ("cost-cap", dict(pool=_result([PASS, HALF, PASS], partial=True, partialReason="cost_ceiling"),
                          code=2)),
        ("sandbox", dict(canary=_result([HALF]))),
        ("missing", dict(doc=None, code=1)),
    ])
    def test_every_failure_mode_reports_only_the_gates_reason_codes(self, env, name, kw):
        codes = {"harness-has-no-eval-runner", "runner-missing", "timeout", "cost-cap", "runner-error",
                 "runs-errored", "runner-sandbox-unwritable", "case-removed-on-branch",
                 "corpus-new-on-branch", "head-moved"}
        env.fake(**kw)
        argv = [] if name != "timeout" else ["--timeout-seconds", "1"]
        assert env.run("c1", *argv) == 0
        assert env.result()["reason"] in codes | {None}
        assert env.result()["verdict"] in {"pass", "fail", "not-run"}
