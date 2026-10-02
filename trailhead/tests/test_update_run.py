"""Tests for trailhead/update_run.py and the apply run lock + `--status` mode.

Contention, SIGKILL and child-outlives-holder rows use a real second process
that takes the lock through the module under test.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from trailhead import cli, update, update_run
from trailhead.cli import main
from trailhead.tests.test_update_apply import (
    _FakeCfg,
    _env,
    _install_stamp,
    _make_runner,
)

RUN_A = "a" * 32
RUN_B = "b" * 32

_HOLDER = """
import os, subprocess, sys
from trailhead import update_run
fd = update_run.acquire_run_lock(sys.argv[1], env=dict(os.environ))
if sys.argv[2] == "child":
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"], close_fds=False
    )
    print(child.pid, flush=True)
else:
    print("ready", flush=True)
sys.stdin.readline()
"""


def _lock_file(tmp_path: Path) -> Path:
    return tmp_path / "state" / "update-run.lock"


class _Holder:
    def __init__(self, tmp_path, run_id=RUN_A, mode="plain"):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _HOLDER, run_id, mode],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=_env(tmp_path),
            text=True,
        )
        self.first_line = self.proc.stdout.readline().strip()
        assert self.first_line, "holder never took the lock"
        self.child_pid = int(self.first_line) if mode == "child" else None

    def release(self):
        if self.proc.poll() is None:
            self.proc.stdin.write("\n")
            self.proc.stdin.flush()
        self.proc.wait(timeout=10)

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=10)
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream:
                stream.close()
        if self.child_pid is not None:
            try:
                os.kill(self.child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.fixture()
def holders():
    made: list[_Holder] = []

    def make(tmp_path, **kw):
        h = _Holder(tmp_path, **kw)
        made.append(h)
        return h

    yield make
    for h in made:
        h.close()


def _pin(monkeypatch, tmp_path):
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)


def _status(capsys, monkeypatch, tmp_path, managed=None):
    _pin(monkeypatch, tmp_path)
    monkeypatch.setattr(
        update_run.outpost_lifecycle, "managed_outpost", lambda env=None, **kw: managed
    )
    monkeypatch.setattr(sys, "argv", ["trailhead", "update", "--status", "--json"])
    rc = main()
    captured = capsys.readouterr()
    return rc, captured


def _apply_kit(tmp_path, monkeypatch):
    env = _env(tmp_path)
    _install_stamp(tmp_path, env)
    monkeypatch.setattr(update, "resolve_config_for_env", lambda env: _FakeCfg())
    monkeypatch.setattr(update, "wire_all_harnesses", lambda *a, **kw: {})
    return env


class TestRunIds:
    def test_fresh_run_ids_are_distinct_32_hex(self):
        a, b = update_run.new_run_id(), update_run.new_run_id()
        assert re.fullmatch(r"[0-9a-f]{32}", a) and re.fullmatch(r"[0-9a-f]{32}", b)
        assert a != b

    @pytest.mark.parametrize(
        "value,ok",
        [
            ("a" * 32, True),
            ("0123456789abcdef0123456789abcdef", True),
            ("a" * 31, False),
            ("a" * 33, False),
            ("A" * 32, False),
            ("g" * 32, False),
            ("a" * 32 + "\n", False),
            ("", False),
        ],
    )
    def test_validate_run_id_shape(self, value, ok):
        assert update_run.validate_run_id(value) is ok


class TestAcquire:
    def test_records_run_id_pid_started_at_in_the_lock_file(self, tmp_path):
        env = _env(tmp_path)
        fd = update_run.acquire_run_lock(RUN_A, env=env)
        try:
            record = json.loads(_lock_file(tmp_path).read_text())
        finally:
            update_run.release_run_lock(fd)
        assert record["run_id"] == RUN_A
        assert record["pid"] == os.getpid()
        assert datetime.fromisoformat(record["started_at"]).tzinfo is not None

    def test_release_leaves_the_file_in_place(self, tmp_path):
        env = _env(tmp_path)
        update_run.release_run_lock(update_run.acquire_run_lock(RUN_A, env=env))
        assert _lock_file(tmp_path).exists()
        assert update_run.read_lock_record(env=env)["run_id"] == RUN_A

    def test_descriptor_is_not_inheritable(self, tmp_path):
        fd = update_run.acquire_run_lock(RUN_A, env=_env(tmp_path))
        try:
            assert os.get_inheritable(fd) is False
        finally:
            update_run.release_run_lock(fd)

    def test_second_acquire_while_held_raises_and_keeps_the_holder_record(self, holders, tmp_path):
        holders(tmp_path, run_id=RUN_A)
        before = _lock_file(tmp_path).read_text()
        with pytest.raises(update_run.RunLockHeld):
            update_run.acquire_run_lock(RUN_B, env=_env(tmp_path))
        assert _lock_file(tmp_path).read_text() == before
        assert json.loads(before)["run_id"] == RUN_A

    def test_a_longer_stale_record_is_fully_replaced_by_the_next_holder(self, tmp_path):
        lock = _lock_file(tmp_path)
        lock.parent.mkdir(parents=True)
        lock.write_text(json.dumps({"run_id": RUN_B, "pid": 1, "started_at": "x" * 500}))
        fd = update_run.acquire_run_lock(RUN_A, env=_env(tmp_path))
        update_run.release_run_lock(fd)
        assert json.loads(lock.read_text())["run_id"] == RUN_A


class TestHolderProbe:
    def test_no_lock_file_means_no_holder_and_no_record(self, tmp_path):
        env = _env(tmp_path)
        assert update_run.current_holder(env=env) is None
        assert update_run.read_lock_record(env=env) is None

    def test_stale_file_is_readable_but_not_a_holder(self, tmp_path):
        env = _env(tmp_path)
        update_run.release_run_lock(update_run.acquire_run_lock(RUN_A, env=env))
        assert update_run.current_holder(env=env) is None
        assert update_run.read_lock_record(env=env)["run_id"] == RUN_A

    def test_live_holder_is_reported_with_its_record(self, holders, tmp_path):
        h = holders(tmp_path, run_id=RUN_A)
        holder = update_run.current_holder(env=_env(tmp_path))
        assert holder["run_id"] == RUN_A
        assert holder["pid"] == h.proc.pid

    def test_probe_does_not_disturb_the_holder_or_block_a_later_acquire(self, tmp_path):
        env = _env(tmp_path)
        update_run.release_run_lock(update_run.acquire_run_lock(RUN_A, env=env))
        update_run.current_holder(env=env)
        update_run.release_run_lock(update_run.acquire_run_lock(RUN_B, env=env))


class TestApplyContention:
    def test_losing_apply_refuses_with_one_line_fetches_nothing_and_keeps_holder_record(
        self, holders, tmp_path, monkeypatch, capsys
    ):
        env = _apply_kit(tmp_path, monkeypatch)
        holders(tmp_path, run_id=RUN_A)
        before = _lock_file(tmp_path).read_text()
        runner, calls = _make_runner()

        rc = update.run_update_apply(env=env, runner=runner, assume_yes=True)

        err = capsys.readouterr().err
        assert rc == 1
        assert not calls
        assert "already running" in err
        assert err.startswith("trailhead: ") and err.count("\n") == 1
        assert _lock_file(tmp_path).read_text() == before

    def test_status_reports_the_real_holder(self, holders, tmp_path, monkeypatch, capsys):
        h = holders(tmp_path, run_id=RUN_A)
        rc, cap = _status(capsys, monkeypatch, tmp_path)
        out = json.loads(cap.out)
        assert rc == 0
        assert out["running"]["run_id"] == RUN_A
        assert out["running"]["pid"] == h.proc.pid

    def test_sigkilled_holder_reads_not_running_and_a_new_apply_takes_the_lock(
        self, holders, tmp_path, monkeypatch, capsys
    ):
        env = _apply_kit(tmp_path, monkeypatch)
        h = holders(tmp_path, run_id=RUN_A)
        h.proc.send_signal(signal.SIGKILL)
        h.proc.wait(timeout=10)

        rc, cap = _status(capsys, monkeypatch, tmp_path)
        assert json.loads(cap.out)["running"] is None

        runner, calls = _make_runner(remote_branch_sha="a" * 40)
        assert update.run_update_apply(env=env, runner=runner, assume_yes=True) == 0
        assert calls

    def test_child_outlives_holder_without_keeping_the_lock(
        self, holders, tmp_path, monkeypatch, capsys
    ):
        env = _apply_kit(tmp_path, monkeypatch)
        h = holders(tmp_path, run_id=RUN_A, mode="child")
        h.release()
        os.kill(h.child_pid, 0)

        rc, cap = _status(capsys, monkeypatch, tmp_path)
        assert json.loads(cap.out)["running"] is None

        runner, calls = _make_runner(remote_branch_sha="a" * 40)
        assert update.run_update_apply(env=env, runner=runner, assume_yes=True) == 0
        assert calls
        os.kill(h.child_pid, 0)


class TestApplyLockPlacement:
    def test_declined_consent_leaves_no_holder_record_and_no_lock_file(
        self, tmp_path, monkeypatch
    ):
        env = _apply_kit(tmp_path, monkeypatch)
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("n\n"))
        runner, calls = _make_runner()

        rc = update.run_update_apply(env=env, runner=runner, is_tty=lambda: True)

        assert rc == 0 and not calls
        assert not _lock_file(tmp_path).exists()

    def test_refused_non_interactive_consent_takes_no_lock(self, tmp_path, monkeypatch):
        env = _apply_kit(tmp_path, monkeypatch)
        runner, calls = _make_runner()
        rc = update.run_update_apply(env=env, runner=runner, is_tty=lambda: False)
        assert rc == 1
        assert not _lock_file(tmp_path).exists()

    def test_lock_is_held_during_the_apply_and_free_after(self, tmp_path, monkeypatch):
        env = _apply_kit(tmp_path, monkeypatch)
        seen: list = []
        inner, _ = _make_runner(remote_branch_sha="a" * 40)

        def runner(args, **kw):
            seen.append(update_run.current_holder(env=env))
            return inner(args, **kw)

        assert update.run_update_apply(env=env, runner=runner, assume_yes=True, run_id=RUN_B) == 0
        assert seen and all(h and h["run_id"] == RUN_B for h in seen)
        assert update_run.current_holder(env=env) is None
        assert update_run.read_lock_record(env=env)["run_id"] == RUN_B

    def test_a_generated_run_id_is_recorded_when_none_is_given(self, tmp_path, monkeypatch):
        env = _apply_kit(tmp_path, monkeypatch)
        runner, _ = _make_runner(remote_branch_sha="a" * 40)
        assert update.run_update_apply(env=env, runner=runner, assume_yes=True) == 0
        assert re.fullmatch(r"[0-9a-f]{32}", update_run.read_lock_record(env=env)["run_id"])

    def test_dry_run_takes_no_lock(self, tmp_path, monkeypatch):
        env = _apply_kit(tmp_path, monkeypatch)
        runner, _ = _make_runner()
        assert update.run_update_apply(env=env, runner=runner, dry_run=True) == 0
        assert not _lock_file(tmp_path).exists()


class TestRunIdFlag:
    def test_valid_run_id_via_cli_is_recorded(self, tmp_path, monkeypatch):
        env = _apply_kit(tmp_path, monkeypatch)
        _pin(monkeypatch, tmp_path)
        monkeypatch.setattr(update, "_default_runner", lambda: _make_runner(remote_branch_sha="a" * 40)[0])
        monkeypatch.setattr(sys, "argv", ["trailhead", "update", "--yes", "--run-id", RUN_B])
        assert main() == 0
        assert update_run.read_lock_record(env=env)["run_id"] == RUN_B

    @pytest.mark.parametrize("bad", ["abc", "A" * 32, "a" * 33, "../" + "a" * 29])
    def test_malformed_run_id_is_refused_before_anything_runs(
        self, tmp_path, monkeypatch, capsys, bad
    ):
        env = _apply_kit(tmp_path, monkeypatch)
        _pin(monkeypatch, tmp_path)
        runner, calls = _make_runner()
        monkeypatch.setattr(update, "_default_runner", lambda: runner)
        monkeypatch.setattr(sys, "argv", ["trailhead", "update", "--yes", "--run-id", bad])
        rc = main()
        assert rc == 1
        assert "run-id" in capsys.readouterr().err
        assert not calls
        assert not _lock_file(tmp_path).exists()

    @pytest.mark.parametrize(
        "mode", [["--status", "--json"], ["--check", "--json"]], ids=["status", "check"]
    )
    def test_malformed_run_id_is_refused_in_every_mode(self, tmp_path, monkeypatch, capsys, mode):
        _pin(monkeypatch, tmp_path)
        ran: list[str] = []
        monkeypatch.setattr(cli, "build_status", lambda *a, **k: ran.append("status") or {})
        monkeypatch.setattr(cli, "check_for_update", lambda *a, **k: ran.append("check"))
        monkeypatch.setattr(sys, "argv", ["trailhead", "update", *mode, "--run-id", "A" * 32])

        rc = main()

        cap = capsys.readouterr()
        assert rc == 1
        assert "run-id" in cap.err
        assert cap.out == ""
        assert ran == []


class TestStatus:
    @pytest.mark.parametrize("managed", [None, {"pid": 4242, "checkout": "/x/outpost"}])
    def test_no_lock_file(self, capsys, monkeypatch, tmp_path, managed):
        rc, cap = _status(capsys, monkeypatch, tmp_path, managed)
        out = json.loads(cap.out)
        assert rc == 0
        assert out["status_schema_version"] == 1
        assert out["managed_outpost"] == managed
        assert out["running"] is None

    @pytest.mark.parametrize("managed", [None, {"pid": 4242, "checkout": "/x/outpost"}])
    def test_stale_lock_file(self, capsys, monkeypatch, tmp_path, managed):
        update_run.release_run_lock(update_run.acquire_run_lock(RUN_A, env=_env(tmp_path)))
        rc, cap = _status(capsys, monkeypatch, tmp_path, managed)
        out = json.loads(cap.out)
        assert out["running"] is None
        assert out["managed_outpost"] == managed

    @pytest.mark.parametrize("managed", [None, {"pid": 4242, "checkout": "/x/outpost"}])
    def test_held_lock(self, capsys, monkeypatch, tmp_path, holders, managed):
        h = holders(tmp_path, run_id=RUN_B)
        rc, cap = _status(capsys, monkeypatch, tmp_path, managed)
        out = json.loads(cap.out)
        assert out["managed_outpost"] == managed
        assert set(out["running"]) == {"run_id", "pid", "started_at"}
        assert out["running"]["run_id"] == RUN_B
        assert out["running"]["pid"] == h.proc.pid
        datetime.fromisoformat(out["running"]["started_at"])

    def test_status_prints_exactly_one_json_object_and_does_no_git(
        self, capsys, monkeypatch, tmp_path
    ):
        def boom(*a, **kw):
            raise AssertionError("status must not run git")

        monkeypatch.setattr(subprocess, "run", boom)
        rc, cap = _status(capsys, monkeypatch, tmp_path)
        assert rc == 0
        assert len(cap.out.strip().splitlines()) == 1
        assert "last_result" in json.loads(cap.out)

    def test_escaping_exception_is_the_usual_trailhead_line_and_non_zero(
        self, capsys, monkeypatch, tmp_path
    ):
        _pin(monkeypatch, tmp_path)

        def explode(env=None, **kw):
            raise RuntimeError("supervisor on fire")

        monkeypatch.setattr(update_run.outpost_lifecycle, "managed_outpost", explode)
        monkeypatch.setattr(sys, "argv", ["trailhead", "update", "--status", "--json"])
        rc = main()
        assert rc != 0
        assert capsys.readouterr().err.startswith("trailhead: ")
