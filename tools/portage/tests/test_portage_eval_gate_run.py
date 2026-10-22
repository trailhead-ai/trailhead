"""`portage eval-gate run` — one planned case through the harness's eval runner.

Every test drives the real ``dispatch.main(["eval-gate", "run", ...])`` (or
``run_case`` with a stub harness) against a fake ``claude`` executable on PATH.
The fake writes a result JSON composed from real runner output
(``fixtures/eval_runner``) and exits with the code the real runner showed; it never
touches the real ``claude``.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from _portage_cli import contained_tempdir  # noqa: F401  (autouse fixture)

from portage.cli import dispatch
from portage.eval_gate.run import run_case
from trailhead.harness import ClaudeCodeHarness, Harness
from trailhead.harness.base import EvalRunner

FIXTURES = Path(__file__).parent / "fixtures" / "eval_runner"
RUNNER = {"model": "sonnet", "allow_tools": ["Read", "Bash(git:*)"], "max_cost_usd": 2.0}


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _run_of(name: str) -> dict:
    return copy.deepcopy(_fixture(name)["cases"][0]["arms"]["with"][0])


PASS, FAIL, ERR = _run_of("scored_pass"), _run_of("scored_fail"), _run_of("unrunnable_timeout")


def _result(runs: list[dict], **top) -> dict:
    doc = _fixture("scored_pass")
    doc["cases"][0]["arms"]["with"] = copy.deepcopy(runs)
    doc.update(top)
    return doc


FAKE = """#!{python}
import json, os, shutil, subprocess, sys, time
LOG = {log!r}
CANARY = "--eval-dir" in sys.argv
open(LOG + ".started", "w").write("1")
json.dump({{"argv": sys.argv, "env": dict(os.environ), "cwd": os.getcwd()}}, open(LOG + ".seen", "w"))
open(LOG + ".calls", "a").write(json.dumps(
    {{"canary": CANARY, "argv": sys.argv, "env": dict(os.environ),
      "tmpdir_exists": os.path.isdir(os.environ.get("TMPDIR", "")),
      "tmpdir_mode": (os.stat(os.environ["TMPDIR"]).st_mode & 0o777) if os.environ.get("TMPDIR") else None}}) + "\\n")
payload, code, sleep = ({canary_payload!r}, {canary_code!r}, {canary_sleep!r}) if CANARY else (
    {payload!r}, {code!r}, {sleep!r})
if {grandchild!r} and not CANARY:
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    open(LOG + ".pid", "w").write(str(p.pid))
if payload:
    out = sys.argv[sys.argv.index("--json") + 1]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if payload == "garbage":
        open(out, "w").write("{{not json")
    else:
        shutil.copy(payload, out)
if sleep:
    time.sleep(sleep)
sys.exit(code)
"""


class Env:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.root = Path(tempfile.mkdtemp(dir="/tmp", prefix="pg"))
        self.home = self.root / "h"
        (self.home / ".claude").mkdir(parents=True)
        self.log = str(tmp_path / "fake")
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        self.state = tmp_path / "state"
        self.state.mkdir()
        self.state.chmod(0o700)
        self.mp = monkeypatch
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("PATH", str(self.bin))
        monkeypatch.setenv("USER", "tester")
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setenv("TMPDIR", str(tmp_path / "inherited-tmp"))
        self.cases = [{"plugin_root": "plugins/p", "case": "c1", "runs": 3, "pass_at": 2,
                       "runner": RUNNER, "new_runner_grants": False}]

    def plan(self, **over) -> None:
        doc = {"schema_version": 1, "status": "cases-selected", "repo": str(self.repo),
               "cases": self.cases}
        doc.update(over)
        (self.state / "plan.json").write_text(json.dumps(doc))

    def _payload(self, doc: dict | str | None, name: str) -> str:
        if isinstance(doc, dict):
            f = self.tmp / name
            f.write_text(json.dumps(doc))
            return str(f)
        return doc or ""

    def fake(self, doc: dict | str | None = None, code: int = 0, sleep: float = 0,
             grandchild: bool = False, canary: dict | str | None = "pass", canary_code: int = 0,
             canary_sleep: float = 0, path: Path | None = None) -> Path:
        """Install the fake runner; the canary (any call with ``--eval-dir``) passes by default."""
        if canary == "pass":
            canary = _result([PASS])
        exe = path or self.bin / "claude"
        exe.write_text(FAKE.format(
            python=sys.executable, log=self.log, payload=self._payload(doc, "payload.json"),
            code=code, sleep=sleep, grandchild=grandchild,
            canary_payload=self._payload(canary, "canary-payload.json"),
            canary_code=canary_code, canary_sleep=canary_sleep))
        exe.chmod(0o755)
        return exe

    @property
    def calls(self) -> list[dict]:
        f = Path(self.log + ".calls")
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []

    def seed_canary(self, verdict="pass") -> None:
        (self.state / "canary.json").write_text(json.dumps(
            {"case": "sandbox-write", "verdict": verdict, "passed": 1, "failed": 0, "errored": 0,
             "reason": None, "runs": []}))

    def run(self, case="c1", *extra) -> int:
        return dispatch.main(["eval-gate", "run", "--state", str(self.state), "--case", case,
                              *extra])

    @property
    def started(self) -> bool:
        return os.path.exists(self.log + ".started")

    @property
    def seen(self) -> dict:
        return json.loads(Path(self.log + ".seen").read_text())

    def result(self, case="c1") -> dict:
        return json.loads((self.state / "results" / f"{case}.json").read_text())


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    e.plan()
    yield e
    shutil.rmtree(e.root, ignore_errors=True)


def _verdict(env, runs, code=1, **top):
    env.fake(_result(runs, **top), code=code)
    assert env.run() == 0
    r = env.result()
    return r["verdict"], r["reason"], (r["passed"], r["failed"], r["errored"])


class TestScoredRuns:
    def test_three_of_three_passing_is_pass(self, env):
        assert _verdict(env, [PASS] * 3, code=0) == ("pass", None, (3, 0, 0))

    def test_two_passing_one_failing_is_pass(self, env):
        assert _verdict(env, [PASS, PASS, FAIL]) == ("pass", None, (2, 1, 0))

    def test_one_passing_two_failing_is_fail(self, env):
        assert _verdict(env, [PASS, FAIL, FAIL]) == ("fail", None, (1, 2, 0))

    def test_two_passing_one_errored_is_pass(self, env):
        assert _verdict(env, [PASS, PASS, ERR]) == ("pass", None, (2, 0, 1))

    def test_one_pass_one_fail_one_errored_is_not_run_runs_errored(self, env):
        assert _verdict(env, [PASS, FAIL, ERR]) == ("not-run", "runs-errored", (1, 1, 1))

    def test_zero_passing_two_failing_one_errored_is_fail(self, env):
        assert _verdict(env, [FAIL, FAIL, ERR]) == ("fail", None, (0, 2, 1))

    def test_pass_at_three_with_two_passing_is_fail(self, env):
        env.cases[0]["pass_at"] = 3
        env.plan()
        assert _verdict(env, [PASS, PASS, FAIL]) == ("fail", None, (2, 1, 0))

    def test_paid_graders_skipped_counts_as_errored(self, env):
        skipped = dict(PASS, skippedPaidGraders=True)
        assert _verdict(env, [PASS, skipped, skipped]) == ("not-run", "runs-errored", (1, 0, 2))

    def test_runs_missing_from_the_result_count_as_errored(self, env):
        assert _verdict(env, [PASS, PASS]) == ("pass", None, (2, 0, 1))
        assert _verdict(env, [PASS, FAIL]) == ("not-run", "runs-errored", (1, 1, 1))

    def test_a_decided_fail_survives_a_cost_ceiling_partial(self, env):
        verdict = _verdict(env, [FAIL, FAIL], code=2, partial=True, partialReason="cost_ceiling")
        assert verdict == ("fail", None, (0, 2, 1))

    def test_result_lists_per_run_grader_verdicts_and_nothing_else(self, env):
        _verdict(env, [PASS, FAIL, ERR])
        runs = env.result()["runs"]
        assert [(r["passed"], r["errored"]) for r in runs] == [(True, False), (False, False),
                                                              (False, True)]
        assert runs[1]["graders"] == [{"name": g["name"], "passed": g["passed"]}
                                      for g in FAIL["graders"]]

    def test_keeps_the_runner_json_in_the_state_dir(self, env):
        _verdict(env, [PASS] * 3)
        assert json.loads((env.state / "runs" / "c1.json").read_text())["cases"]


class TestRunnerFailures:
    def test_missing_runner_binary(self, env):
        assert env.run() == 0
        r = env.result()
        assert (r["verdict"], r["reason"]) == ("not-run", "runner-missing")

    def test_a_runner_that_cannot_be_executed_is_runner_missing(self, env):
        exe = env.bin / "claude"
        exe.write_text("not a program\n")
        exe.chmod(0o755)
        assert env.run() == 0
        r = env.result()
        assert (r["verdict"], r["reason"]) == ("not-run", "runner-missing")

    def test_a_removed_case_starts_no_runner(self, env):
        env.cases[0]["case_removed"] = True
        env.plan()
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        r = env.result()
        assert (r["verdict"], r["reason"], r["errored"]) == ("not-run", "case-removed-on-branch", 3)
        assert env.calls == []

    def test_harness_without_a_hook(self, env):
        class Bare(Harness):
            name = "bare"
            detect = classmethod(lambda cls, e: True)

            def __getattr__(self, n):
                raise AssertionError(n)

        for abstract in Harness.__abstractmethods__:
            setattr(Bare, abstract, lambda self, *a, **k: None)
        Bare.__abstractmethods__ = frozenset()
        result = run_case(env.state, "c1", harnesses=[Bare()], env={"HOME": str(env.home)})
        assert (result["verdict"], result["reason"]) == ("not-run", "harness-has-no-eval-runner")
        assert env.result()["reason"] == "harness-has-no-eval-runner"

    def test_no_detected_harness_has_no_eval_runner(self, env):
        result = run_case(env.state, "c1", harnesses=[], env={"HOME": str(env.home)})
        assert result["reason"] == "harness-has-no-eval-runner"

    def test_timeout_kills_the_runner_and_its_children(self, env):
        env.fake(sleep=60, grandchild=True)
        start = time.monotonic()
        assert env.run("c1", "--timeout-seconds", "2") == 0
        assert time.monotonic() - start < 20
        r = env.result()
        assert (r["verdict"], r["reason"]) == ("not-run", "timeout")
        pid = int(Path(env.log + ".pid").read_text())
        for _ in range(40):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            pytest.fail("grandchild survived the timeout")

    def test_a_decided_fail_is_not_downgraded_when_the_runner_is_killed(self, env):
        env.fake(_result([FAIL, FAIL, ERR]), sleep=60)
        assert env.run("c1", "--timeout-seconds", "2") == 0
        r = env.result()
        assert (r["verdict"], r["reason"], r["failed"]) == ("fail", None, 2)

    def test_a_stale_result_json_is_not_read_after_a_timeout(self, env):
        (env.state / "runs").mkdir()
        (env.state / "runs" / "c1.json").write_text(json.dumps(_result([PASS] * 3)))
        env.fake(sleep=60)
        assert env.run("c1", "--timeout-seconds", "1") == 0
        assert env.result()["reason"] == "timeout"

    def test_cost_cap_when_nothing_is_decidable(self, env):
        doc = _fixture("partial_cost_ceiling")
        env.fake(doc, code=2)
        assert env.run() == 0
        r = env.result()
        assert (r["verdict"], r["reason"], r["errored"]) == ("not-run", "cost-cap", 3)

    def test_auth_failed_partial_is_runner_error(self, env):
        env.fake(_fixture("partial_auth_failed"), code=2)
        assert env.run() == 0
        assert env.result()["reason"] == "runner-error"

    def test_nonzero_exit_without_json_is_runner_error(self, env):
        env.fake(None, code=1)
        assert env.run() == 0
        assert env.result()["reason"] == "runner-error"

    def test_nonzero_exit_with_unparseable_json_is_runner_error(self, env):
        env.fake("garbage", code=1)
        assert env.run() == 0
        assert env.result()["reason"] == "runner-error"

    @pytest.mark.parametrize("name", ["unrunnable_scaffold_exit1", "unrunnable_invalid_model",
                                      "unrunnable_network", "unrunnable_timeout"])
    def test_unrunnable_results_are_runs_errored(self, env, name):
        env.fake(_fixture(name), code=1)
        assert env.run() == 0
        r = env.result()
        assert (r["verdict"], r["reason"], r["passed"], r["failed"]) == (
            "not-run", "runs-errored", 0, 0)


class TestRefusals:
    def test_case_absent_from_plan(self, env):
        env.fake(_result([PASS] * 3))
        assert env.run("nope") == 2
        assert not env.started

    def test_case_name_shape_is_revalidated(self, env):
        env.cases[0]["case"] = "../evil"
        env.plan()
        env.fake(_result([PASS] * 3))
        assert env.run("../evil") == 2
        assert not env.started
        assert not (env.state / "results").exists()

    def test_symlinked_state_dir(self, env, capsys):
        link = env.tmp / "link"
        link.symlink_to(env.state)
        env.fake(_result([PASS] * 3))
        assert dispatch.main(["eval-gate", "run", "--state", str(link), "--case", "c1"]) == 2
        assert "is a symlink" in capsys.readouterr().err
        assert not env.started

    def test_group_readable_state_dir(self, env):
        env.state.chmod(0o750)
        env.fake(_result([PASS] * 3))
        assert env.run() == 2
        assert not env.started

    def test_foreign_owned_state_dir(self, env):
        env.mp.setattr(os, "geteuid", lambda: os.stat(env.state).st_uid + 1)
        env.fake(_result([PASS] * 3))
        assert env.run() == 2
        assert not env.started

    def test_plugin_root_escaping_the_repo(self, env):
        env.cases[0]["plugin_root"] = "../outside"
        env.plan()
        env.fake(_result([PASS] * 3))
        assert env.run() == 2
        assert not env.started

    def test_timeout_above_the_ceiling(self, env):
        env.fake(_result([PASS] * 3))
        assert env.run("c1", "--timeout-seconds", "601") == 2
        assert not env.started


class TestChildEnvironment:
    def test_only_allowlisted_variables_reach_the_runner(self, env):
        for k in ("GH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "LORE_STATE_DIR"):
            env.mp.setenv(k, "secret")
        env.mp.setenv("LANG", "en_US.UTF-8")
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        child = env.seen["env"]
        assert set(child) <= {"PATH", "HOME", "USER", "LANG", "TERM", "PWD", "SHLVL", "_",
                              "LC_CTYPE", "TMPDIR"}
        assert child["HOME"] == str(env.home)
        assert child["USER"] == "tester"
        assert child["LANG"] == "en_US.UTF-8"
        for k in ("GH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "LORE_STATE_DIR"):
            assert k not in child


class TestArgv:
    def test_argv_is_built_from_the_plan_and_state(self, env):
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        argv = env.seen["argv"][1:]
        assert argv[:3] == ["plugin", "eval", str(env.repo / "plugins/p")]

        def after(flag):
            return argv[argv.index(flag) + 1]

        assert after("--case") == "c1"
        assert after("--ablation") == "none"
        assert after("--model") == "sonnet"
        assert after("--runs") == "3"
        assert after("-j") == "3"
        assert after("--max-cost-usd") == "2.0"
        assert after("--json") == str(env.state / "runs" / "c1.json")
        assert after("--output-dir") == str(env.state / "runs" / "c1") + "/"
        grants = argv[argv.index("--allow-tools") + 1:]
        assert grants[:2] == ["Read", "Bash(git:*)"] and grants[2].startswith("--")
        for flag in ("--scaffold", "--trust-plugin", "--no-publish"):
            assert flag in argv

    def test_parallelism_is_capped_at_eight(self, env):
        env.cases[0]["runs"] = 12
        env.plan()
        env.fake(_result([PASS] * 12))
        assert env.run() == 0
        argv = env.seen["argv"]
        assert argv[argv.index("-j") + 1] == "8"


class TestHarnessSelection:
    def test_the_first_detected_harness_supplies_the_runner(self, env):
        env.fake(_result([PASS] * 3))
        env.seed_canary()

        class Stub:
            def __init__(self, tag):
                self.tag = tag
                self.calls = 0

            def eval_runner(self, **kw):
                self.calls += 1
                return EvalRunner(argv=[str(env.bin / "claude"), self.tag], env={})

        first, second = Stub("first"), Stub("second")
        run_case(env.state, "c1", harnesses=[first, second], env={"HOME": str(env.home)})
        assert (first.calls, second.calls) == (1, 0)
        assert env.seen["argv"][1] == "first"


    def test_the_default_path_takes_the_first_detected_harness(self, env):
        env.fake(_result([PASS] * 3))
        seen_env = {}

        class Stub:
            def __init__(self, tag):
                self.tag = tag

            def eval_runner(self, **kw):
                return EvalRunner(argv=[str(env.bin / "claude"), self.tag], env={})

        def detect(e):
            seen_env.update(e)
            return [Stub("first"), Stub("second")]

        env.mp.setattr("trailhead.harness.detect_harnesses", detect)
        run_case(env.state, "c1", env={"HOME": str(env.home)})
        assert env.seen["argv"][1] == "first"
        assert seen_env == {"HOME": str(env.home)}


def _children(base: Path) -> list[Path]:
    return sorted(base.iterdir()) if base.exists() else []


class TestRunnerTempDir:
    def test_each_call_gets_a_private_dir_under_the_cache_base_and_outside_tmp(self, env):
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        base = env.home / ".cache" / "pegt"
        assert len(env.calls) == 2
        dirs = []
        for call in env.calls:
            d = Path(call["env"]["TMPDIR"])
            assert d.parent == base
            assert (call["tmpdir_exists"], call["tmpdir_mode"]) == (True, 0o700)
            dirs.append(d)
        assert dirs[0] != dirs[1]
        assert (base.stat().st_mode & 0o777) == 0o700

    def test_the_dirs_are_gone_after_the_call(self, env):
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        assert _children(env.home / ".cache" / "pegt") == []

    def test_the_dirs_are_gone_after_a_timeout(self, env):
        env.fake(_result([PASS] * 3), sleep=30)
        env.seed_canary()
        run_case(env.state, "c1", timeout_seconds=1)
        assert Path(env.calls[0]["env"]["TMPDIR"]).parent.name == "pegt"
        assert _children(env.home / ".cache" / "pegt") == []

    def test_the_dirs_are_gone_after_the_runner_raises(self, env):
        env.fake(_result([PASS] * 3))
        env.seed_canary()
        seen = []

        def boom(runner, state, timeout):
            seen.append(runner.env["TMPDIR"])
            raise RuntimeError("boom")

        env.mp.setattr("portage.eval_gate.run._execute", boom)
        with pytest.raises(RuntimeError):
            run_case(env.state, "c1")
        assert Path(seen[0]).parent.name == "pegt"
        assert _children(env.home / ".cache" / "pegt") == []

    def test_only_the_per_call_dir_is_removed(self, env):
        env.fake(_result([PASS] * 3))
        base = env.home / ".cache" / "pegt"
        base.mkdir(parents=True, mode=0o700)
        (base / "other").mkdir()
        assert env.run() == 0
        assert _children(base) == [base / "other"]

    def test_xdg_cache_home_is_honoured(self, env):
        xdg = env.root / "x"
        env.mp.setenv("XDG_CACHE_HOME", str(xdg))
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        assert Path(env.calls[0]["env"]["TMPDIR"]).parent == xdg / "pegt"
        assert not (env.home / ".cache").exists()

    def test_a_relative_xdg_cache_home_falls_back_to_home(self, env):
        env.mp.setenv("XDG_CACHE_HOME", "relative/cache")
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        assert Path(env.calls[0]["env"]["TMPDIR"]).parent == (
            env.home / ".cache" / "pegt")

    def test_an_inherited_tmpdir_never_reaches_the_child(self, env):
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        for call in env.calls:
            assert call["env"]["TMPDIR"] != str(env.tmp / "inherited-tmp")

    def _refused(self, env):
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        r = env.result()
        assert (r["verdict"], r["reason"]) == ("not-run", "runner-error")
        assert env.calls == [] and not env.started

    def test_a_symlinked_base_is_refused_and_no_runner_starts(self, env):
        base = env.home / ".cache" / "pegt"
        base.parent.mkdir(parents=True)
        real = env.tmp / "real"
        real.mkdir(mode=0o700)
        base.symlink_to(real)
        self._refused(env)

    def test_a_group_writable_base_is_refused_and_no_runner_starts(self, env):
        base = env.home / ".cache" / "pegt"
        base.mkdir(parents=True)
        base.chmod(0o770)
        self._refused(env)

    def test_a_foreign_owned_base_is_refused_and_no_runner_starts(self, env):
        base = env.home / ".cache" / "pegt"
        base.mkdir(parents=True, mode=0o700)
        env.mp.setattr("portage.eval_gate.run._check_state", lambda state: None)
        env.mp.setattr(os, "geteuid", lambda: os.stat(base).st_uid + 1)
        self._refused(env)


class TestRunnerTempDirLength:
    """The runner's sandbox wrote only when the temp path was at most 51 characters."""

    def _cache_of_call_path_length(self, env, n: int) -> Path:
        # per-call path = cache + "/pegt/" + 8 random characters
        cache = env.root
        pad = n - 14 - len(str(cache)) - 1
        cache = cache / ("c" * pad)
        env.mp.setenv("XDG_CACHE_HOME", str(cache))
        assert len(str(cache)) + 14 == n
        return cache

    def test_the_base_is_short_enough_for_a_typical_home(self, env):
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        assert all(len(c["env"]["TMPDIR"]) <= 51 for c in env.calls)
        assert Path(env.calls[0]["env"]["TMPDIR"]).parent == env.home / ".cache" / "pegt"

    def test_a_per_call_path_of_51_characters_runs(self, env):
        self._cache_of_call_path_length(env, 51)
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        assert [len(c["env"]["TMPDIR"]) for c in env.calls] == [51, 51]
        assert env.result()["verdict"] == "pass"

    def test_a_per_call_path_of_52_characters_is_unwritable_and_no_runner_starts(self, env, capsys):
        self._cache_of_call_path_length(env, 52)
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        r = env.result()
        assert (r["verdict"], r["reason"]) == ("not-run", "runner-sandbox-unwritable")
        assert env.calls == [] and not env.started
        err = capsys.readouterr().err
        assert "52" in err and "XDG_CACHE_HOME" in err
        assert not (env.state / "canary.json").exists()


class TestSeamSmoke:
    def test_claude_code_runner_program_resolves_on_runner_path(self, env):  # inert-gate: allow seam smoke
        env.fake(_result([PASS] * 3))
        runner = ClaudeCodeHarness().eval_runner(
            plugin_dir=env.repo, case="c1", runs=3, model="sonnet", allow_tools=["Read"],
            max_cost_usd=1.0, json_path=env.state / "r.json", output_dir=env.state / "o",
            env={"PATH": str(env.bin), "HOME": str(env.home)})
        assert shutil.which(runner.argv[0], path=runner.env["PATH"]) == str(env.bin / "claude")


class TestCorpusNewOnBranch:
    def test_a_case_of_a_corpus_new_on_the_branch_is_not_run_and_starts_no_runner(self, env):
        env.cases[0]["new_runner_grants"] = True
        env.plan()
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        r = env.result()
        assert (r["verdict"], r["reason"], r["errored"]) == ("not-run", "corpus-new-on-branch", 3)
        assert env.calls == []
        assert not (env.state / "canary.json").exists()

    def test_a_case_of_a_reviewed_corpus_still_runs(self, env):
        env.fake(_result([PASS] * 3))
        assert env.run() == 0
        assert env.result()["verdict"] == "pass"


class TestUnexpectedErrors:
    def test_a_crash_inside_the_run_records_runner_error_and_exits_non_zero(self, env, monkeypatch, capsys):
        from portage.eval_gate import canary

        def boom(state):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(canary, "read_cached", boom)
        env.fake(_result([PASS] * 3))
        assert env.run() == 1
        r = env.result()
        assert (r["verdict"], r["reason"], r["errored"]) == ("not-run", "runner-error", 3)
        assert "Traceback" not in capsys.readouterr().err

    def test_a_refusal_is_still_exit_2_and_records_nothing(self, env):
        assert env.run("nope") == 2
        assert not (env.state / "results").exists()


class TestChildIsReaped:
    def test_an_exception_after_launch_kills_the_runner_process_group(self, env, monkeypatch):
        import subprocess

        env.fake(sleep=60, grandchild=True)
        real_wait = subprocess.Popen.wait
        calls = []

        def interrupted(self, timeout=None):
            if calls:
                return real_wait(self, timeout)
            calls.append(1)
            for _ in range(100):
                if Path(env.log + ".pid").exists():
                    break
                time.sleep(0.1)
            raise RuntimeError("interrupted")

        monkeypatch.setattr(subprocess.Popen, "wait", interrupted)
        env.seed_canary()
        with pytest.raises(RuntimeError, match="interrupted"):
            run_case(env.state, "c1")
        pid = int(Path(env.log + ".pid").read_text())
        for _ in range(40):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            pytest.fail("the runner's process group survived the interrupt")
