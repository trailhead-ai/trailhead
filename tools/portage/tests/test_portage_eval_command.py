"""`portage eval start | wait` on the local backend.

Every test drives the real ``dispatch.main(["eval", ...])`` with ``--backend local`` (so it holds
when run from inside the sandbox), a stub runner pinned through ``PORTAGE_EVAL_RUNNER``, and the
real detached worker. The stub blocks on files in a control directory the test releases, so the
tests can observe a request mid-run. A started worker lives as long as the pytest process does.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import signal
import stat
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from _portage_cli import contained_tempdir  # noqa: F401  (autouse fixture)

from portage.cli import dispatch
from portage.eval_core import execute
from portage.eval_command import local, request

FIXTURES = Path(__file__).parent / "fixtures" / "eval_runner"
ID = re.compile(r"^local-[a-z0-9]{12}$")
MAPPING = {
    "schema_version": 1,
    "runner": {"model": "sonnet", "allow_tools": ["Read"], "max_cost_usd": 2.0},
    "cases": {name: {"measures": ["skills"]} for name in ("c1", "c2", "c3")},
}

STUB = '''#!{python}
import json, os, subprocess, sys, time
CTL, FIX = {ctl!r}, {fixtures!r}
argv = sys.argv
canary = "--eval-dir" in argv
case = "sandbox-write" if canary else argv[argv.index("--case") + 1]
out = argv[argv.index("--json") + 1]
asked = int(argv[argv.index("--runs") + 1])


def flag(name):
    path = os.path.join(CTL, name)
    return open(path).read().strip() if os.path.exists(path) else None


open(os.path.join(CTL, "canary-started" if canary else "case-started-" + case), "a").write(
    str(os.getpid()) + "\\n")
mode = flag("canary-mode" if canary else "mode-" + case) or "pass"
if not canary and flag("hold") is not None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    open(os.path.join(CTL, "child.pid"), "w").write(str(child.pid))
    while not os.path.exists(os.path.join(CTL, "release")):
        time.sleep(0.02)
fixture = {{"pass": "scored_pass", "fail": "scored_fail", "err": "unrunnable_timeout"}}[mode]
run = json.load(open(os.path.join(FIX, fixture + ".json")))["cases"][0]["arms"]["with"][0]
doc = json.load(open(os.path.join(FIX, "scored_pass.json")))
doc["cases"][0]["arms"]["with"] = [run] * asked
os.makedirs(os.path.dirname(out), exist_ok=True)
json.dump(doc, open(out, "w"))
'''


def until(predicate, seconds=30.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("timed out waiting for the condition")


class Ws:
    def __init__(self, tmp_path, home, capsys):
        self.home, self.capsys = home, capsys
        self.ctl = tmp_path / "ctl"
        self.ctl.mkdir()
        self.repo = tmp_path / "repo"
        plugin = self.repo / "plugins" / "p"
        (plugin / "skills").mkdir(parents=True)
        (plugin / "skills" / "x.md").write_text("x")
        for name in MAPPING["cases"]:
            (plugin / "evals" / name).mkdir(parents=True)
            (plugin / "evals" / name / "case.yaml").write_text("name: x\n")
        (plugin / "evals" / "eval-gate.json").write_text(json.dumps(MAPPING))
        self.stub = tmp_path / "runner"
        self.stub.write_text(STUB.format(python=sys.executable, ctl=str(self.ctl),
                                         fixtures=str(FIXTURES)))
        self.stub.chmod(0o755)

    def set(self, name, value=""):
        (self.ctl / name).write_text(value)

    def release(self):
        self.set("release")

    def count(self, name):
        path = self.ctl / name
        return len(path.read_text().split()) if path.exists() else 0

    def main(self, *argv):
        self.capsys.readouterr()
        rc = dispatch.main(list(argv))
        out = self.capsys.readouterr()
        return rc, out.out, out.err

    def start(self, *cases, plugin="plugins/p", extra=()):
        argv = ["eval", "start", "--repo", str(self.repo), "--plugin", plugin,
                "--backend", "local", *extra]
        for c in cases:
            argv += ["--case", c]
        return self.main(*argv)

    def started(self, *cases):
        rc, out, err = self.start(*cases)
        assert rc == 0, (out, err)
        return re.search(r"^request: (\S+)$", out, re.M).group(1)

    def wait(self, request_id, seconds=60):
        return self.main("eval", "wait", request_id, "--timeout-seconds", str(seconds))

    @property
    def eval_root(self):
        return self.home / ".local" / "state" / "portage" / "eval"

    def req(self, request_id):
        return self.eval_root / request_id

    def worker_pid(self, request_id):
        return int((self.req(request_id) / "worker.pid").read_text())

    def case_result(self, request_id, case):
        return json.loads((self.req(request_id) / "results" / f"{case}.json").read_text())


HOME_LIMIT = 30


def _short_home(base=None):
    base = Path.home() / ".cache" if base is None else base
    for _ in range(64):
        home = base / ("e" + uuid.uuid4().hex[:3])
        if len(str(home)) > HOME_LIMIT:
            pytest.fail(f"the throwaway home {home} is {len(str(home))} characters; the runner's "
                        f"51-character temp path outside /tmp needs at most {HOME_LIMIT}")
        try:
            (home / ".claude").mkdir(parents=True)
        except FileExistsError:
            continue
        return home
    pytest.fail(f"no unused throwaway home under {base}")


def _reap(ws):
    groups = []
    if ws.eval_root.exists():
        for pidfile in ws.eval_root.glob("*/worker.pid"):
            try:
                groups.append(int(pidfile.read_text()))
            except ValueError:
                pass
    for pgid in groups:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    child = ws.ctl / "child.pid"
    if child.exists():
        try:
            os.kill(int(child.read_text()), signal.SIGKILL)
        except (ProcessLookupError, ValueError):
            pass
    for pgid in groups:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.waitpid(pgid, os.WNOHANG)
            except ChildProcessError:
                pass
            try:
                os.killpg(pgid, 0)
            except (ProcessLookupError, PermissionError):
                break
            time.sleep(0.01)


@contextlib.contextmanager
def _workspace(tmp_path, monkeypatch, capsys):
    home = _short_home()
    try:
        for var in ("XDG_STATE_HOME", "XDG_CACHE_HOME", "CLAUDE_CONFIG_DIR", "SANDBOX_RUNTIME"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HOME", str(home))
        w = Ws(tmp_path, home, capsys)
        monkeypatch.setenv("PORTAGE_EVAL_RUNNER", str(w.stub))
        try:
            yield w
        finally:
            _reap(w)
    finally:
        shutil.rmtree(home, ignore_errors=True)


@pytest.fixture
def ws(tmp_path, monkeypatch, capsys):
    with _workspace(tmp_path, monkeypatch, capsys) as w:
        yield w


class TestThrowawayHome:
    def test_a_home_that_fits_the_runner_temp_limit_is_created_and_usable(self):
        home = _short_home()
        try:
            assert len(str(home)) <= HOME_LIMIT and (home / ".claude").is_dir()
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_a_home_too_long_for_the_runner_temp_limit_fails_naming_the_length_and_creates_nothing(
            self, tmp_path):
        base = tmp_path / ("x" * HOME_LIMIT)
        with pytest.raises(pytest.fail.Exception, match=r"is \d+ characters") as raised:
            _short_home(base)
        assert f"is {len(str(base)) + 5} characters" in str(raised.value)
        assert not base.exists()

    def test_the_home_is_removed_when_the_test_using_it_fails(self, tmp_path, monkeypatch, capsys):
        with pytest.raises(RuntimeError):
            with _workspace(tmp_path, monkeypatch, capsys) as w:
                home = w.home
                assert home.exists()
                raise RuntimeError("the test body failed")
        assert not home.exists()


class TestStart:
    def test_start_returns_the_identifier_while_the_stub_is_still_blocked_and_the_request_later_finishes(self, ws):
        ws.set("hold")
        rc, out, _ = ws.start("c1")
        assert rc == 0
        request_id = re.search(r"^request: (\S+)$", out, re.M).group(1)
        assert ID.match(request_id)
        until(lambda: ws.count("case-started-c1"))
        assert not (ws.req(request_id) / "result.json").exists()
        ws.release()
        rc, out, _ = ws.wait(request_id)
        assert rc == 0
        assert "case c1: pass" in out

    def test_a_started_request_prints_the_paste_ready_wait_command(self, ws):
        ws.set("hold")
        rc, out, _ = ws.start("c1")
        request_id = re.search(r"^request: (\S+)$", out, re.M).group(1)
        assert rc == 0
        assert f"wait: portage eval wait {request_id}" in out.splitlines()

    def test_the_request_directory_is_private_and_records_the_request_and_the_worker(self, ws):
        ws.set("hold")
        request_id = ws.started("c1", "c2")
        req = ws.req(request_id)
        assert stat.S_IMODE(os.stat(req).st_mode) == 0o700
        doc = json.loads((req / "request.json").read_text())
        assert doc["backend"] == "local"
        assert doc["repo"] == str(ws.repo) and doc["plugin_root"] == "plugins/p"
        assert [c["case"] for c in doc["cases"]] == ["c1", "c2"]
        assert doc["cases"][0]["runs"] == 3 and doc["cases"][0]["pass_at"] == 2
        assert doc["cases"][0]["runner"] == MAPPING["runner"]
        os.kill(ws.worker_pid(request_id), 0)

    def test_two_named_cases_run_and_the_third_in_the_mapping_does_not(self, ws):
        request_id = ws.started("c1", "c2")
        rc, out, _ = ws.wait(request_id)
        assert rc == 0
        assert "case c1: pass" in out and "case c2: pass" in out and "c3" not in out
        assert ws.count("case-started-c1") == 1 and ws.count("case-started-c2") == 1
        assert ws.count("case-started-c3") == 0

    def test_an_undeclared_case_is_refused_by_name_and_creates_no_request_directory(self, ws):
        rc, out, err = ws.start("c1", "nope")
        assert rc == 2
        assert "nope" in err and "request:" not in out
        assert not ws.eval_root.exists() or list(ws.eval_root.iterdir()) == []
        assert ws.count("case-started-c1") == 0

    @pytest.mark.parametrize("plugin", ["../x", "/etc", "plugins/missing"])
    def test_a_plugin_root_that_leaves_the_repo_or_has_no_mapping_is_refused(self, ws, plugin):
        rc, out, err = ws.start("c1", plugin=plugin)
        assert rc == 2 and "request:" not in out and err

    def test_a_plugin_root_with_dotdot_that_reaches_a_real_plugin_outside_the_repo_is_refused(self, ws, tmp_path):
        shutil.copytree(ws.repo / "plugins" / "p", tmp_path / "outside")
        rc, out, err = ws.start("c1", plugin="../outside")
        assert rc == 2 and "request:" not in out

    def test_a_plugin_root_symlinked_out_of_the_repo_is_refused(self, ws, tmp_path):
        outside = tmp_path / "outside"
        shutil.copytree(ws.repo / "plugins" / "p", outside)
        (ws.repo / "plugins" / "link").symlink_to(outside)
        rc, out, err = ws.start("c1", plugin="plugins/link")
        assert rc == 2 and "request:" not in out

    def test_a_mapping_that_does_not_load_is_refused_naming_the_file(self, ws):
        (ws.repo / "plugins" / "p" / "evals" / "eval-gate.json").write_text("{nope")
        rc, out, err = ws.start("c1")
        assert rc == 2 and "eval-gate.json" in err

    def test_a_start_that_cannot_create_its_request_is_not_run_runner_broken_with_exit_3(self, ws):
        state = ws.home / ".local" / "state"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("a file where the state directory should be")
        rc, out, err = ws.start("c1")
        assert rc == 3
        assert "not run: runner-broken" in out and "next step: stop and report" in out
        assert "request:" not in out


class TestWorker:
    def test_a_failing_canary_reports_every_case_not_run_runner_broken_and_never_starts_a_case(self, ws):
        ws.set("canary-mode", "fail")
        request_id = ws.started("c1", "c2")
        rc, out, _ = ws.wait(request_id)
        assert rc == 3
        for case in ("c1", "c2"):
            r = ws.case_result(request_id, case)
            assert (r["verdict"], r["reason"], r["next_step"]) == (
                "not-run", "runner-broken", "stop and report")
        assert out.count("not run: runner-broken") == 2
        assert out.count("next step: stop and report") == 2
        assert ws.count("case-started-c1") == 0 and ws.count("case-started-c2") == 0

    def test_a_second_request_after_a_failed_canary_runs_the_canary_again(self, ws):
        ws.set("canary-mode", "fail")
        ws.wait(ws.started("c1"))
        assert ws.count("canary-started") == 1
        ws.wait(ws.started("c1"))
        assert ws.count("canary-started") == 2

    def test_the_canary_runs_once_per_request_not_once_per_case(self, ws):
        ws.wait(ws.started("c1", "c2"))
        assert ws.count("canary-started") == 1

    @pytest.mark.parametrize("home_kind", ["under-tmp", "over-51-characters"])
    def test_a_temp_base_under_tmp_or_over_51_characters_is_runner_broken_and_starts_no_runner(
            self, ws, monkeypatch, tmp_path, home_kind):
        if home_kind == "under-tmp":
            odd = Path(tempfile.mkdtemp(dir="/tmp", prefix="pg"))
        else:
            odd = Path.home() / ".cache" / ("x" * 36)
        (odd / ".claude").mkdir(parents=True)
        try:
            monkeypatch.setenv("HOME", str(odd))
            request_id = ws.started("c1")
            ws.home = odd
            rc, out, _ = ws.wait(request_id)
            assert rc == 3 and "not run: runner-broken" in out
            assert ws.count("canary-started") == 0 and ws.count("case-started-c1") == 0
        finally:
            _reap(ws)
            shutil.rmtree(odd, ignore_errors=True)

    def test_the_runner_temp_base_comes_from_home_never_from_xdg_cache_home(self, ws, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(Path("/tmp") / "xdg-cache-should-be-ignored"))
        rc, out, _ = ws.wait(ws.started("c1"))
        assert rc == 0 and "case c1: pass" in out
        assert (ws.home / ".cache" / "pegt").is_dir()

    def test_a_worker_that_raises_still_writes_result_json_as_not_run_runner_broken(self, ws, tmp_path):
        req = tmp_path / "req"
        req.mkdir(mode=0o700)
        (req / "request.json").write_text(json.dumps({
            "schema_version": 1, "id": "local-aaaaaaaaaaaa", "backend": "local",
            "repo": str(ws.repo), "plugin_root": "plugins/p",
            "cases": [{"case": "c1", "runs": 3, "pass_at": 2, "runner": MAPPING["runner"]},
                      {"case": "c2", "runs": 3, "pass_at": 2, "runner": MAPPING["runner"]}]}))

        def explode(runner, cwd, timeout):
            raise RuntimeError("boom")

        local.run_worker(req, env={**os.environ}, execute=explode)
        result = json.loads((req / "result.json").read_text())
        assert [(c["case"], c["verdict"], c["reason"], c["next_step"]) for c in result["cases"]] == [
            ("c1", "not-run", "runner-broken", "stop and report"),
            ("c2", "not-run", "runner-broken", "stop and report")]

    def test_a_worker_that_raises_after_deciding_a_case_keeps_that_case_and_breaks_the_rest(self, ws):
        request_id = ws.started("c1")
        ws.wait(request_id)
        req = ws.req(request_id)
        doc = json.loads((req / "request.json").read_text())
        doc["cases"].append({**doc["cases"][0], "case": "c2"})
        (req / "request.json").write_text(json.dumps(doc))
        for stale in [req / "result.json", *(req / "results").iterdir()]:
            stale.unlink()
        calls = []

        def real_for_the_canary_and_c1_then_explode(runner, cwd, timeout):
            calls.append(1)
            if len(calls) > 2:
                raise RuntimeError("boom")
            return execute(runner, cwd, timeout)

        local.run_worker(req, env={**os.environ}, execute=real_for_the_canary_and_c1_then_explode)
        result = json.loads((req / "result.json").read_text())
        assert [(c["case"], c["verdict"], c["reason"]) for c in result["cases"]] == [
            ("c1", "pass", None), ("c2", "not-run", "runner-broken")]
        assert (req / "results" / "c1.json").exists() and not (req / "results" / "c2.json").exists()


class TestWait:
    def test_wait_prints_each_cases_verdict_and_each_runs_score_with_a_summary_first(self, ws):
        ws.set("mode-c1", "fail")
        ws.set("mode-c2", "pass")
        request_id = ws.started("c1", "c2")
        rc, out, _ = ws.wait(request_id)
        lines = out.splitlines()
        assert lines[0] == f"request: {request_id}"
        assert lines[1] == "summary: 1 failed, 1 passed"
        assert "case c1: fail" in out and "case c2: pass" in out
        c1 = out.split("case c1: fail")[1].split("case c2")[0]
        c2 = out.split("case c2: pass")[1]
        assert c1.count("score 0.5") == 2 and "run 1: score 0.5 failed" in c1
        assert c2.count("score 1") == 2 and "run 1: score 1 passed" in c2

    def test_wait_exits_0_when_every_case_passed(self, ws):
        rc, out, _ = ws.wait(ws.started("c1", "c2"))
        assert rc == 0 and "summary: 2 passed" in out

    def test_wait_exits_1_when_one_case_failed_and_another_was_not_run(self, ws):
        ws.set("mode-c1", "fail")
        ws.set("mode-c2", "err")
        rc, out, _ = ws.wait(ws.started("c1", "c2"))
        assert rc == 1
        assert "summary: 1 failed, 1 not run" in out
        assert "not run: runs-errored" in out and "next step: stop and report" in out

    def test_wait_exits_3_when_nothing_failed_and_a_case_was_not_run(self, ws):
        ws.set("mode-c2", "err")
        rc, out, _ = ws.wait(ws.started("c1", "c2"))
        assert rc == 3
        assert "summary: 1 not run, 1 passed" in out

    def test_wait_exits_4_while_the_stub_is_still_blocked_and_says_how_to_wait_again(self, ws):
        ws.set("hold")
        request_id = ws.started("c1", "c2")
        until(lambda: ws.count("case-started-c1"))
        rc, out, _ = ws.wait(request_id, seconds=1)
        assert rc == 4
        assert f"request: {request_id}" in out
        assert f"wait again: portage eval wait {request_id}" in out
        assert "0 of 2 cases finished" in out

    def test_wait_reports_the_cases_finished_so_far_while_it_still_runs(self, ws):
        ws.set("hold")
        ws.release()
        request_id = ws.started("c1", "c2")
        ws.wait(request_id)
        (ws.req(request_id) / "result.json").unlink()
        (ws.req(request_id) / "results" / "c2.json").unlink()
        with open(ws.req(request_id) / local.LOCK_NAME, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            rc, out, _ = ws.wait(request_id, seconds=1)
        assert rc == 4 and "1 of 2 cases finished (c1)" in out

    @pytest.mark.parametrize("bad", ["local-nosuchrequest", "local-aaaaaaaaaaaa"])
    def test_wait_on_an_unknown_identifier_exits_2(self, ws, bad):
        rc, out, err = ws.wait(bad)
        assert rc == 2 and err

    def test_wait_with_no_bound_returns_within_110_seconds_of_a_request_that_never_finishes(
            self, ws, monkeypatch):
        ws.set("hold")
        request_id = ws.started("c1")
        until(lambda: ws.count("case-started-c1"))
        now = [1000.0]
        monkeypatch.setattr(local, "CLOCK", lambda: now[0])
        monkeypatch.setattr(local, "SLEEP", lambda s: now.__setitem__(0, now[0] + s))
        rc, out, _ = ws.main("eval", "wait", request_id)
        assert rc == 4
        assert 100 <= now[0] - 1000.0 <= 110

    @pytest.mark.parametrize("seconds", ["541", "0", "-5"])
    def test_a_timeout_outside_1_to_540_is_refused(self, ws, seconds):
        request_id = ws.started("c1")
        rc, out, err = ws.main("eval", "wait", request_id, "--timeout-seconds", seconds)
        assert rc == 2 and "540" in err

    def test_the_bounds_540_and_1_are_accepted(self, ws):
        request_id = ws.started("c1")
        ws.wait(request_id, seconds=60)
        assert ws.main("eval", "wait", request_id, "--timeout-seconds", "540")[0] == 0
        assert ws.main("eval", "wait", request_id, "--timeout-seconds", "1")[0] == 0

    def test_wait_fed_a_truncated_result_json_reports_no_verdict_from_it(self, ws):
        ws.set("hold")
        request_id = ws.started("c1")
        until(lambda: ws.count("case-started-c1"))
        good = json.dumps({"schema_version": 1, "cases": [
            {"case": "c1", "verdict": "pass", "reason": None, "passed": 2, "failed": 0,
             "errored": 0, "next_step": None, "runs": []}]})
        (ws.req(request_id) / "result.json").write_text(good[: len(good) // 2])
        rc, out, _ = ws.wait(request_id, seconds=1)
        assert rc == 4
        assert "pass" not in out

    def test_a_truncated_result_json_from_a_dead_worker_is_interrupted_not_a_verdict(self, ws):
        ws.set("hold")
        request_id = ws.started("c1")
        until(lambda: ws.count("case-started-c1"))
        os.kill(ws.worker_pid(request_id), signal.SIGKILL)
        (ws.req(request_id) / "result.json").write_text('{"schema_version": 1, "cases": [{"ca')
        rc, out, _ = ws.wait(request_id, seconds=10)
        assert rc == 3 and "not run: interrupted" in out

    def test_a_result_json_of_the_wrong_shape_is_not_a_verdict(self, ws):
        ws.set("hold")
        request_id = ws.started("c1")
        until(lambda: ws.count("case-started-c1"))
        (ws.req(request_id) / "result.json").write_text(json.dumps({"cases": [{"case": "c1", "verdict": "banana"}]}))
        rc, out, _ = ws.wait(request_id, seconds=1)
        assert rc == 4 and "banana" not in out

    @pytest.mark.parametrize("bad", [
        "../../x", "local-ABCDEFGHIJKL", "local-abc", "/etc/passwd", "local-abcdefghijkl/../x",
        "local-abcdefghijkl\n", "x-abcdefghijkl", ""])
    def test_off_shape_identifiers_exit_2_and_read_nothing(self, ws, bad):
        decoy = ws.home / ".local" / "state" / "x"
        decoy.mkdir(parents=True)
        ws.eval_root.mkdir(parents=True)
        leaked = {"case": "leak", "verdict": "pass", "reason": None, "passed": 1, "failed": 0,
                  "errored": 0, "next_step": None, "runs": []}
        (decoy / "request.json").write_text(json.dumps({"cases": [{"case": "leak", "runs": 1}]}))
        (decoy / "result.json").write_text(json.dumps({"cases": [leaked]}))
        before = sorted(p.name for p in (ws.home / ".local" / "state").iterdir())
        rc, out, err = ws.main("eval", "wait", bad)
        assert rc == 2 and "leak" not in out + err
        assert sorted(p.name for p in (ws.home / ".local" / "state").iterdir()) == before

    def test_an_off_shape_identifier_creates_nothing_even_in_a_home_with_no_state_directory(self, ws):
        rc, _, _ = ws.main("eval", "wait", "../../x")
        assert rc == 2
        assert not (ws.home / ".local").exists()


class TestInterruptedWorker:
    def test_a_killed_worker_leaves_its_unfinished_cases_not_run_interrupted_with_exit_3_never_4(self, ws):
        ws.set("hold")
        request_id = ws.started("c1", "c2")
        until(lambda: ws.count("case-started-c1"))
        os.kill(ws.worker_pid(request_id), signal.SIGKILL)
        rc, out, _ = ws.wait(request_id, seconds=30)
        assert rc == 3
        assert out.count("not run: interrupted") == 2
        assert out.count("next step: resubmit") == 2
        assert "summary: 2 not run" in out

    def test_a_dead_worker_whose_pid_is_now_a_live_unrelated_process_is_interrupted_not_running(self, ws):
        ws.set("hold")
        request_id = ws.started("c1")
        until(lambda: ws.count("case-started-c1"))
        os.kill(ws.worker_pid(request_id), signal.SIGKILL)
        (ws.req(request_id) / "worker.pid").write_text(f"{os.getpid()}\n")
        rc, out, _ = ws.wait(request_id, seconds=10)
        assert rc == 3
        assert "not run: interrupted" in out

    def test_a_killed_worker_keeps_the_cases_that_finished_before_it_died(self, ws):
        request_id = ws.started("c1")
        ws.wait(request_id)
        req = ws.req(request_id)
        doc = json.loads((req / "request.json").read_text())
        doc["cases"].append({**doc["cases"][0], "case": "c2"})
        (req / "request.json").write_text(json.dumps(doc))
        (req / "result.json").unlink()
        ws.set("hold")
        child = shutil_which_dead_pid()
        (req / "worker.pid").write_text(str(child))
        rc, out, _ = ws.wait(request_id, seconds=10)
        assert rc == 3
        assert "case c1: pass" in out and "case c2: not run" in out and "not run: interrupted" in out

    def test_sigterm_stops_the_runner_process_group_and_the_cases_are_not_run_interrupted(self, ws):
        ws.set("hold")
        request_id = ws.started("c1")
        until(lambda: ws.count("case-started-c1") and (ws.ctl / "child.pid").exists())
        runner_pid = int((ws.ctl / "case-started-c1").read_text().split()[0])
        child_pid = int((ws.ctl / "child.pid").read_text())
        os.kill(ws.worker_pid(request_id), signal.SIGTERM)
        until(lambda: not _alive(runner_pid) and not _alive(child_pid))
        rc, out, _ = ws.wait(request_id, seconds=30)
        assert rc == 3 and "not run: interrupted" in out


def shutil_which_dead_pid():
    import subprocess

    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


class TestRequestInterface:
    def test_the_not_run_block_is_one_function_start_and_wait_both_print(self, ws, capsys):
        request.print_not_run_block("runner-broken")
        out = capsys.readouterr().out
        assert out == "not run: runner-broken\nnext step: stop and report\n"
        request.print_not_run_block("interrupted", indent="  ")
        assert capsys.readouterr().out == "  not run: interrupted\n  next step: resubmit\n"


SHIM = "/home/someone/.local/state/trailhead/bin/portage-eval"


class TestBackendChoice:
    def _argv(self, ws, *extra):
        return ["eval", "start", "--repo", str(ws.repo), "--plugin", "plugins/p", "--case", "c1",
                "--case", "c2", *extra]

    def test_the_sandbox_marker_with_no_backend_is_not_run_could_not_leave_sandbox_and_starts_nothing(
            self, ws, monkeypatch):
        monkeypatch.setenv("SANDBOX_RUNTIME", "1")
        rc, out, err = ws.main(*self._argv(ws))
        assert rc == 3, (out, err)
        assert "not run: could-not-leave-sandbox" in out.splitlines()
        assert "request:" not in out
        assert not ws.eval_root.exists() or list(ws.eval_root.iterdir()) == []
        assert ws.count("canary-started") == 0

    def test_without_the_marker_and_with_no_backend_the_request_runs_on_the_local_backend(self, ws):
        rc, out, err = ws.main(*self._argv(ws))
        assert rc == 0, (out, err)
        request_id = re.search(r"^request: (\S+)$", out, re.M).group(1)
        assert json.loads((ws.req(request_id) / "request.json").read_text())["backend"] == "local"
        rc, out, _ = ws.wait(request_id)
        assert rc == 0 and "case c1: pass" in out

    def test_an_explicit_local_backend_runs_even_with_the_sandbox_marker(self, ws, monkeypatch):
        monkeypatch.setenv("SANDBOX_RUNTIME", "1")
        request_id = ws.started("c1")
        rc, out, _ = ws.wait(request_id)
        assert rc == 0 and "case c1: pass" in out

    def test_through_the_shim_the_message_names_the_cause_and_the_expanded_shim_command(self, ws, monkeypatch):
        monkeypatch.setenv("SANDBOX_RUNTIME", "1")
        monkeypatch.setenv("PORTAGE_EVAL_SHIM", SHIM)
        rc, out, _ = ws.main(*self._argv(ws))
        assert rc == 3
        assert "next step: stop and report" in out.splitlines()
        assert "chezmoi apply" in out and "new session" in out
        assert f"{SHIM} start --plugin plugins/p --case c1 --case c2 --repo {ws.repo}" in out

    def test_not_through_the_shim_with_the_default_shim_installed_prints_the_paste_ready_command(
            self, ws, monkeypatch):
        monkeypatch.setenv("SANDBOX_RUNTIME", "1")
        monkeypatch.delenv("PORTAGE_EVAL_SHIM", raising=False)
        default = ws.home / ".local" / "state" / "trailhead" / "bin" / "portage-eval"
        default.parent.mkdir(parents=True)
        default.write_text("#!/bin/sh\nexit 0\n")
        default.chmod(0o755)
        rc, out, _ = ws.main(*self._argv(ws))
        assert rc == 3
        assert f"run outside the sandbox: {default} start --plugin plugins/p --case c1 --case c2 --repo {ws.repo}" in out
        assert "not installed" not in out

    def test_not_through_the_shim_with_no_default_shim_says_it_is_not_installed_and_names_no_command(
            self, ws, monkeypatch):
        monkeypatch.setenv("SANDBOX_RUNTIME", "1")
        monkeypatch.delenv("PORTAGE_EVAL_SHIM", raising=False)
        missing = ws.home / ".local" / "state" / "trailhead" / "bin" / "portage-eval"
        assert not missing.exists()
        rc, out, _ = ws.main(*self._argv(ws))
        assert rc == 3
        assert "not run: could-not-leave-sandbox" in out.splitlines()
        assert "next step: stop and report" in out.splitlines()
        assert "not installed" in out and "trailhead update" in out
        assert "claude" in out and "chezmoi apply" in out and "new session" in out
        assert "run outside the sandbox:" not in out
        assert str(missing) not in out

    def test_a_default_shim_that_is_not_an_executable_file_is_treated_as_not_installed(
            self, ws, monkeypatch):
        monkeypatch.setenv("SANDBOX_RUNTIME", "1")
        monkeypatch.delenv("PORTAGE_EVAL_SHIM", raising=False)
        default = ws.home / ".local" / "state" / "trailhead" / "bin" / "portage-eval"
        default.parent.mkdir(parents=True)
        default.write_text("#!/bin/sh\nexit 0\n")
        default.chmod(0o644)
        _, out, _ = ws.main(*self._argv(ws))
        assert "not installed" in out and "run outside the sandbox:" not in out

    def test_a_shim_path_with_a_space_is_quoted_in_the_command_to_paste(self, ws, monkeypatch):
        monkeypatch.setenv("SANDBOX_RUNTIME", "1")
        monkeypatch.setenv("PORTAGE_EVAL_SHIM", "/home/a b/bin/portage-eval")
        _, out, _ = ws.main(*self._argv(ws))
        assert "'/home/a b/bin/portage-eval' start --plugin plugins/p" in out

    @pytest.mark.parametrize("bad", ["portage-eval", "relative/portage-eval", "/x/y\nz", ""])
    def test_a_shim_path_that_is_not_absolute_or_has_a_control_character_is_not_named(
            self, ws, monkeypatch, bad):
        monkeypatch.setenv("SANDBOX_RUNTIME", "1")
        monkeypatch.setenv("PORTAGE_EVAL_SHIM", bad)
        rc, out, _ = ws.main(*self._argv(ws))
        assert rc == 3
        assert "cause:" not in out
        assert not re.search(rf"(^|\s){re.escape(bad)} start", out)

    def test_the_wait_command_names_the_absolute_shim_when_run_through_it(self, ws, monkeypatch):
        ws.set("hold")
        monkeypatch.setenv("PORTAGE_EVAL_SHIM", SHIM)
        request_id = ws.started("c1")
        rc, out, _ = ws.main("eval", "start", "--repo", str(ws.repo), "--plugin", "plugins/p",
                             "--case", "c2", "--backend", "local")
        second = re.search(r"^request: (\S+)$", out, re.M).group(1)
        assert f"wait: {SHIM} wait {second}" in out.splitlines()
        rc, out, _ = ws.wait(request_id, seconds=1)
        assert rc == 4 and f"wait again: {SHIM} wait {request_id}" in out
        ws.release()

    def test_a_runner_that_has_moved_since_the_shim_was_built_is_runner_broken_next_step_run_trailhead_update(
            self, ws, monkeypatch, tmp_path):
        monkeypatch.setenv("PORTAGE_EVAL_RUNNER", str(tmp_path / "gone" / "claude"))
        request_id = ws.started("c1")
        rc, out, _ = ws.wait(request_id)
        assert rc == 3
        assert "not run: runner-broken" in out and "next step: run trailhead update" in out
        assert ws.count("canary-started") == 0
        assert json.loads((ws.req(request_id) / "results" / "c1.json").read_text())[
            "next_step"] == "run trailhead update"

    def test_a_failing_canary_still_says_stop_and_report(self, ws):
        ws.set("canary-mode", "fail")
        request_id = ws.started("c1")
        rc, out, _ = ws.wait(request_id)
        assert rc == 3 and "next step: stop and report" in out


class TestPinnedShimEndToEnd:
    def _shim(self, ws, tmp_path):
        from trailhead.install import build_cli_shims, resolve_cli_tools
        from trailhead.pathint import resolve_shim_dir

        stubbin = tmp_path / "stubbin"
        stubbin.mkdir()
        (stubbin / "claude").symlink_to(ws.stub)
        env = {"HOME": str(ws.home), "PATH": f"{stubbin}:/usr/bin:/bin"}
        build_cli_shims(resolve_cli_tools({"portage": True}), env=env)
        return resolve_shim_dir(env=env) / "portage-eval"

    def _run(self, shim, ws, *argv, marker=True):
        import subprocess

        env = {"HOME": str(ws.home), "PATH": "/usr/bin:/bin"}
        if marker:
            env["SANDBOX_RUNTIME"] = "1"
        return subprocess.run([str(shim), *argv], capture_output=True, text=True, env=env, timeout=120)

    def test_the_shim_starts_a_request_and_waits_for_its_verdict_with_the_stub_runner(self, ws, tmp_path):
        shim = self._shim(ws, tmp_path)
        started = self._run(shim, ws, "start", "--repo", str(ws.repo), "--plugin", "plugins/p",
                            "--case", "c1", "--backend", "local")
        assert started.returncode == 0, started.stderr
        request_id = re.search(r"^request: (\S+)$", started.stdout, re.M).group(1)
        assert f"wait: {shim} wait {request_id}" in started.stdout.splitlines()
        waited = self._run(shim, ws, "wait", request_id, "--timeout-seconds", "100")
        assert waited.returncode == 0, (waited.stdout, waited.stderr)
        assert "case c1: pass" in waited.stdout
        assert ws.count("case-started-c1") == 1

    def test_the_shim_with_the_sandbox_marker_and_no_backend_tells_the_session_to_stop(self, ws, tmp_path):
        shim = self._shim(ws, tmp_path)
        proc = self._run(shim, ws, "start", "--repo", str(ws.repo), "--plugin", "plugins/p", "--case", "c1")
        assert proc.returncode == 3, (proc.stdout, proc.stderr)
        assert "not run: could-not-leave-sandbox" in proc.stdout
        assert f"{shim} start --plugin plugins/p --case c1 --repo {ws.repo}" in proc.stdout
        assert "chezmoi apply" in proc.stdout
        assert not ws.eval_root.exists() or list(ws.eval_root.iterdir()) == []

    def test_the_shim_without_the_marker_runs_locally_with_no_backend_flag(self, ws, tmp_path):
        shim = self._shim(ws, tmp_path)
        started = self._run(shim, ws, "start", "--repo", str(ws.repo), "--plugin", "plugins/p",
                            "--case", "c1", marker=False)
        assert started.returncode == 0, started.stderr
        request_id = re.search(r"^request: (\S+)$", started.stdout, re.M).group(1)
        assert self._run(shim, ws, "wait", request_id, "--timeout-seconds", "100").returncode == 0

    def test_a_runner_moved_after_the_build_surfaces_as_runner_broken_with_run_trailhead_update(
            self, ws, tmp_path):
        shim = self._shim(ws, tmp_path)
        moved = tmp_path / "moved"
        ws.stub.rename(moved)
        started = self._run(shim, ws, "start", "--repo", str(ws.repo), "--plugin", "plugins/p",
                            "--case", "c1", "--backend", "local")
        request_id = re.search(r"^request: (\S+)$", started.stdout, re.M).group(1)
        waited = self._run(shim, ws, "wait", request_id, "--timeout-seconds", "100")
        assert waited.returncode == 3, (waited.stdout, waited.stderr)
        assert "not run: runner-broken" in waited.stdout
        assert "next step: run trailhead update" in waited.stdout
