"""The sandbox-write canary and the ``PORTAGE_EVAL_RUNNER`` override of `portage eval-gate run`.

Every test drives the real ``dispatch.main(["eval-gate", "run", ...])`` against a fake
runner executable that logs every invocation (``Env.calls``); the canary is the one call
whose argv carries ``--eval-dir``. The real ``claude`` is never started.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from _portage_cli import contained_tempdir  # noqa: F401  (autouse fixture)

from portage.eval_gate import canary, unmapped_runner_cases
from test_portage_eval_gate_plan import _commit, _corpus, _git, _plan, _write
from test_portage_eval_gate_run import ERR, FAIL, PASS, Env, _fixture, _result

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    e.plan()
    return e


def _canary_calls(env):
    return [c for c in env.calls if c["canary"]]


def _case_calls(env):
    return [c for c in env.calls if not c["canary"]]


class TestCanaryGatesTheCase:
    def test_a_passing_canary_lets_the_case_run_and_be_decided(self, env):
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        assert [c["canary"] for c in env.calls] == [True, False]
        r = env.result()
        assert (r["verdict"], r["reason"], r["passed"]) == ("pass", None, 3)

    def test_a_scored_fail_canary_is_runner_sandbox_unwritable_and_no_case_starts(self, env):
        env.fake(_result([PASS] * 3), canary=_result([FAIL]), canary_code=1)
        assert env.run() == 0
        assert _case_calls(env) == []
        r = env.result()
        assert (r["verdict"], r["reason"]) == ("not-run", "runner-sandbox-unwritable")

    def test_an_errored_canary_records_its_own_reason_and_no_case_starts(self, env):
        env.fake(_result([PASS] * 3), canary=_result([ERR]), canary_code=1)
        assert env.run() == 0
        assert _case_calls(env) == []
        r = env.result()
        assert (r["verdict"], r["reason"]) == ("not-run", "runs-errored")

    def test_a_canary_killed_by_the_bound_records_timeout_and_no_case_starts(self, env):
        env.fake(_result([PASS] * 3), canary=None, canary_sleep=60)
        start = time.monotonic()
        assert env.run("c1", "--timeout-seconds", "2") == 0
        assert time.monotonic() - start < 20
        assert _case_calls(env) == []
        r = env.result()
        assert (r["verdict"], r["reason"]) == ("not-run", "timeout")

    def test_a_canary_that_hits_the_cost_cap_records_cost_cap(self, env):
        env.fake(_result([PASS] * 3), canary=_fixture("partial_cost_ceiling"), canary_code=2)
        assert env.run() == 0
        assert _case_calls(env) == []
        assert env.result()["reason"] == "cost-cap"

    def test_a_missing_runner_is_runner_missing_for_the_canary_too(self, env):
        assert env.run() == 0
        assert env.calls == []
        assert env.result()["reason"] == "runner-missing"

    def test_the_canary_is_bounded_by_its_own_limit_below_the_total(self, env):
        env.mp.setattr(canary, "TIMEOUT_SECONDS", 1)
        env.fake(_result([PASS] * 3), canary=None, canary_sleep=60)
        start = time.monotonic()
        assert env.run("c1", "--timeout-seconds", "30") == 0
        assert time.monotonic() - start < 10
        assert _case_calls(env) == []
        assert env.result()["reason"] == "timeout"

    def test_the_canary_and_the_case_share_the_total_bound(self, env):
        env.fake(None, canary_sleep=1.5, sleep=60)
        start = time.monotonic()
        assert env.run("c1", "--timeout-seconds", "3") == 0
        assert time.monotonic() - start < 4.5
        assert (env.result()["verdict"], env.result()["reason"]) == ("not-run", "timeout")


class TestCanaryRequest:
    def test_one_cheap_pinned_run_of_the_shipped_canary_case(self, env):
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        argv = _canary_calls(env)[0]["argv"]

        def after(flag):
            return argv[argv.index(flag) + 1]

        plugin = Path(argv[3])
        assert plugin == REPO_ROOT / "tools" / "portage" / "plugins" / "portage"
        assert after("--eval-dir") == "eval-canary"
        assert after("--case") == "sandbox-write"
        assert after("--runs") == "1"
        assert after("--model") == "claude-haiku-5-5"
        assert argv[argv.index("--allow-tools") + 1] == "Bash(touch *)"
        assert after("--json") == str(env.state / "canary-run.json")

    def test_the_shipped_canary_case_is_a_runnable_case_for_the_runner(self, env):
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        plugin = Path(_canary_calls(env)[0]["argv"][3])
        case = plugin / "eval-canary" / "sandbox-write"
        name = re.search(r"^name:\s*(\S+)\s*$", (case / "case.yaml").read_text(), re.M).group(1)
        assert name == "sandbox-write" == canary.CASE
        created = re.search(r"`touch (\S+)`", (case / "prompt.md").read_text()).group(1)
        graded = [
            re.search(r"^path:\s*(\S+)\s*$", g.read_text(), re.M).group(1)
            for g in (case / "graders").iterdir()
        ]
        assert graded == [created]


class TestCanaryIsCached:
    def test_two_runs_in_one_state_dir_invoke_the_canary_once(self, env):
        env.cases.append(dict(env.cases[0], case="c2"))
        env.plan()
        env.fake(_result([PASS] * 3))
        assert env.run("c1") == 0
        assert env.run("c2") == 0
        assert len(_canary_calls(env)) == 1
        assert len(_case_calls(env)) == 2

    def test_the_cached_result_is_what_the_second_case_is_gated_on(self, env):
        env.cases.append(dict(env.cases[0], case="c2"))
        env.plan()
        env.fake(_result([PASS] * 3), canary=_result([FAIL]), canary_code=1)
        assert env.run("c1") == 0
        assert env.run("c2") == 0
        assert len(_canary_calls(env)) == 1
        assert env.result("c2")["reason"] == "runner-sandbox-unwritable"

    def test_canary_json_records_the_decision(self, env):
        env.fake(_result([PASS] * 3), canary=_result([FAIL]), canary_code=1)
        assert env.run() == 0
        doc = json.loads((env.state / "canary.json").read_text())
        assert (doc["case"], doc["verdict"], doc["reason"]) == ("sandbox-write", "fail", None)
        assert set(doc) == {"case", "verdict", "passed", "failed", "errored", "reason", "runs"}

    def test_an_unreadable_cache_runs_the_canary_again(self, env):
        (env.state / "canary.json").write_text("{not json")
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        assert len(_canary_calls(env)) == 1
        assert json.loads((env.state / "canary.json").read_text())["verdict"] == "pass"


class TestRunnerOverride:
    def test_a_valid_override_is_the_program_for_both_canary_and_case(self, env):
        pinned = env.fake(_result([PASS] * 3), path=env.tmp / "pinned-claude")
        env.mp.setenv("PORTAGE_EVAL_RUNNER", str(pinned))
        assert not (env.bin / "claude").exists()
        assert env.run() == 0
        assert [c["canary"] for c in env.calls] == [True, False]
        assert env.calls[0]["argv"][0] == env.calls[1]["argv"][0] == str(pinned)
        assert env.result()["verdict"] == "pass"

    def test_the_override_never_reaches_the_child_environment(self, env):
        pinned = env.fake(_result([PASS] * 3), path=env.tmp / "pinned-claude")
        env.mp.setenv("PORTAGE_EVAL_RUNNER", str(pinned))
        assert env.run() == 0
        assert len(env.calls) == 2
        for call in env.calls:
            assert "PORTAGE_EVAL_RUNNER" not in call["env"]

    @pytest.mark.parametrize("kind", ["relative", "missing", "not-executable", "directory"])
    def test_an_unusable_override_is_runner_missing_and_nothing_starts(self, env, kind, capsys):
        env.fake(_result([PASS] * 3))
        env.mp.chdir(env.tmp)
        plain = env.tmp / "plain"
        plain.write_text("x")
        plain.chmod(0o644)
        value = {"relative": "bin/claude", "missing": str(env.tmp / "nope"),
                 "not-executable": str(plain), "directory": str(env.tmp)}[kind]
        env.mp.setenv("PORTAGE_EVAL_RUNNER", value)
        assert env.run() == 0
        assert env.calls == []
        r = env.result()
        assert (r["verdict"], r["reason"]) == ("not-run", "runner-missing")
        assert "PORTAGE_EVAL_RUNNER" in capsys.readouterr().err

    def test_an_unset_override_uses_the_harnesss_own_lookup(self, env):
        env.fake(_result([PASS] * 3))
        env.mp.delenv("PORTAGE_EVAL_RUNNER", raising=False)
        assert env.run() == 0
        assert [c["argv"][0] for c in env.calls] == [str(env.bin / "claude")] * 2

    def test_a_pinned_binary_that_cannot_write_is_caught_by_the_canary(self, env):
        pinned = env.fake(_result([PASS] * 3), canary=_result([FAIL]), canary_code=1,
                          path=env.tmp / "pinned-claude")
        env.mp.setenv("PORTAGE_EVAL_RUNNER", str(pinned))
        assert env.run() == 0
        assert _case_calls(env) == []
        assert env.result()["reason"] == "runner-sandbox-unwritable"


class TestCanaryIsNeverAGatedCase:
    def _repo_with_canary(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        _corpus(repo, "plugins/portage", {"alpha": {"measures": ["skills/a.md"]}})
        _commit(repo, "base")
        _git(repo, "branch", "base")
        _git(repo, "checkout", "-b", "feature")
        shipped = canary.plugin_dir() / canary.EVAL_DIR
        files = [f for f in shipped.rglob("*") if f.is_file()]
        assert files
        for f in files:
            _write(repo, f"plugins/portage/{canary.EVAL_DIR}/{f.relative_to(shipped)}", f.read_text())
        _commit(repo, "canary")
        return repo

    def test_a_branch_touching_only_the_shipped_canary_maps_no_cases(self, tmp_path, capsys):
        repo = self._repo_with_canary(tmp_path)
        rc, plan, _ = _plan(repo, capsys, "--base", "base")
        assert (rc, plan["status"], plan["cases"]) == (0, "no-cases-mapped", [])

    def test_the_corpus_beside_the_canary_reports_no_unmapped_case(self, tmp_path):
        repo = self._repo_with_canary(tmp_path)
        assert unmapped_runner_cases(repo / "plugins" / "portage" / "evals") == []


class TestOverrideTrust:
    def test_a_group_writable_override_is_runner_missing_naming_the_variable(self, env, capsys):
        pinned = env.fake(_result([PASS] * 3), path=env.tmp / "pinned-claude")
        pinned.chmod(0o775)
        env.mp.setenv("PORTAGE_EVAL_RUNNER", str(pinned))
        assert env.run() == 0
        assert env.calls == []
        assert env.result()["reason"] == "runner-missing"
        assert "PORTAGE_EVAL_RUNNER" in capsys.readouterr().err

    def test_a_world_writable_override_is_refused(self, env):
        pinned = env.fake(_result([PASS] * 3), path=env.tmp / "pinned-claude")
        pinned.chmod(0o757)
        env.mp.setenv("PORTAGE_EVAL_RUNNER", str(pinned))
        assert env.run() == 0
        assert env.calls == []
        assert env.result()["reason"] == "runner-missing"

    def test_a_symlink_is_judged_by_its_target(self, env):
        target = env.fake(_result([PASS] * 3), path=env.tmp / "real-claude")
        target.chmod(0o777)
        link = env.tmp / "link-claude"
        link.symlink_to(target)
        env.mp.setenv("PORTAGE_EVAL_RUNNER", str(link))
        assert env.run() == 0
        assert env.calls == []
        assert env.result()["reason"] == "runner-missing"

    def test_a_symlink_to_a_private_target_is_accepted(self, env):
        target = env.fake(_result([PASS] * 3), path=env.tmp / "real-claude")
        link = env.tmp / "link-claude"
        link.symlink_to(target)
        env.mp.setenv("PORTAGE_EVAL_RUNNER", str(link))
        assert env.run() == 0
        assert env.result()["verdict"] == "pass"

    def test_an_override_owned_by_someone_else_is_refused(self, env, monkeypatch):
        from portage.eval_gate import run as run_mod

        pinned = env.fake(_result([PASS] * 3), path=env.tmp / "pinned-claude")
        env.mp.setenv("PORTAGE_EVAL_RUNNER", str(pinned))
        monkeypatch.setattr(run_mod, "_trusted_uids", lambda: {424242})
        assert env.run() == 0
        assert env.calls == []
        assert env.result()["reason"] == "runner-missing"
