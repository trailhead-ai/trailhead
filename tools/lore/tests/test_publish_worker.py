"""Behavioral proof of the ``lore publish`` worker (``lore.cli.publish``).

Debounce, single-flight, and marker lifecycle are the worker's OWN bookkeeping
and are exercised through an injected clock/sleeper/sync_call — no test here
ever sleeps for real, and the sync engine's own git behavior is stubbed for
these cases (``sync.py`` has its own suite). ``TestRealSyncWiring`` below is
the one test that runs the real, unstubbed ``cmd_sync`` against a real git
vault + bare remote, proving the worker's default wiring is genuine and not
just its bookkeeping.

Convention (Axiom 6, matching ``test_vault_write_lock.py``): the cross-process
contention test spawns a subprocess to hold the worker's own lock file for
real — that is the actual single-flight guarantee.
"""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import CLI_PATH, make_bare_remote, make_git_vault, write_vault_config
from test_sync_multi_vault import _wire_remote
from test_vault_write_lock import _spawn_holder

from lore.cli import publish
from lore.cli import resolve_state


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class FakeClock:
    """An injectable clock: ``now`` advances only when told to."""

    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingSleeper:
    """An injectable sleeper that advances a :class:`FakeClock` instead of blocking."""

    def __init__(self, clock: FakeClock, *, on_sleep=None):
        self.clock = clock
        self.calls: list[float] = []
        self.on_sleep = on_sleep

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self.on_sleep is not None:
            self.on_sleep(len(self.calls), seconds)
        self.clock.advance(seconds)


def _make_vault(tmp_path: Path, name: str, *, config_home: Path) -> Path:
    """A committed git vault, seeded into config.json under *name*."""
    vault = make_git_vault(tmp_path / name)
    _add_vault_to_config(config_home, name, vault)
    return vault


def _add_vault_to_config(config_home: Path, name: str, vault: Path) -> None:
    lore_cfg = config_home / "lore" / "config.json"
    vaults = []
    if lore_cfg.exists():
        import json

        existing = json.loads(lore_cfg.read_text())
        vaults = [(v["name"], v["scope"], v["path"]) for v in existing["vaults"]]
    vaults.append((name, "default" if not vaults else "product", str(vault)))
    write_vault_config(config_home, vaults)


def _stub_sync_call(outcome: str, *, rc: int = 0, side_effect=None):
    calls: list[str] = []

    def _call(vault_name: str):
        calls.append(vault_name)
        if side_effect is not None:
            side_effect()
        return rc, outcome

    _call.calls = calls
    return _call


@pytest.fixture
def env(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    config_home = tmp_path / "config"
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    return SimpleNamespace(tmp_path=tmp_path, state_dir=state_dir, config_home=config_home)


# ---------------------------------------------------------------------------
# quiet window
# ---------------------------------------------------------------------------


class TestQuietWindow:
    def test_stamp_older_than_window_runs_immediately_without_waiting(self, env):
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        clock = FakeClock()
        publish.touch_request_stamp(vault, clock=lambda: clock() - 10.0)
        sleeper = RecordingSleeper(clock)
        sync_call = _stub_sync_call("converged")

        rc = publish.cmd_publish(SimpleNamespace(
            vault="default", quiet_for=5.0, clock=clock, sleeper=sleeper, sync_call=sync_call,
        ))

        assert rc == 0
        assert sleeper.calls == [], "a stamp already outside the window must not wait"
        assert sync_call.calls == ["default"]

    def test_stamp_inside_window_waits_until_quiet(self, env):
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        clock = FakeClock()
        publish.touch_request_stamp(vault, clock=clock)
        sleeper = RecordingSleeper(clock)
        sync_call = _stub_sync_call("converged")

        rc = publish.cmd_publish(SimpleNamespace(
            vault="default", quiet_for=5.0, clock=clock, sleeper=sleeper, sync_call=sync_call,
        ))

        assert rc == 0
        assert sleeper.calls, "a fresh stamp must wait before syncing"
        assert sum(sleeper.calls) >= 5.0
        assert sync_call.calls == ["default"]

    def test_refresh_during_the_wait_extends_it_past_the_original_deadline(self, env):
        """A second write landing mid-wait pushes the deadline out — the loop
        waits relative to the LATEST stamp, not the one it started with."""
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        clock = FakeClock()
        publish.touch_request_stamp(vault, clock=clock)

        def _refresh_once(call_count, seconds):
            if call_count == 1:
                # A new write lands just as the original wait would have
                # ended — the stamp reads as "just now" once this sleep
                # advances the clock to that point.
                publish.touch_request_stamp(vault, clock=lambda: clock.now + seconds)

        sleeper = RecordingSleeper(clock, on_sleep=_refresh_once)
        sync_call = _stub_sync_call("converged")

        rc = publish.cmd_publish(SimpleNamespace(
            vault="default", quiet_for=5.0, clock=clock, sleeper=sleeper, sync_call=sync_call,
        ))

        assert rc == 0
        assert len(sleeper.calls) >= 2, "the refresh must force at least one more wait"
        assert sync_call.calls == ["default"]

    def test_explicit_quiet_for_zero_publishes_now(self, env):
        """`--quiet-for 0` must mean publish NOW, not fall back to the 5s
        default — `0.0 or DEFAULT_QUIET_FOR` treats 0 as falsy and silently
        restores the default, which is the bug this pins."""
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        clock = FakeClock()
        publish.touch_request_stamp(vault, clock=clock)  # stamp is "now" — fresh
        sleeper = RecordingSleeper(clock)
        sync_call = _stub_sync_call("converged")

        rc = publish.cmd_publish(SimpleNamespace(
            vault="default", quiet_for=0.0, clock=clock, sleeper=sleeper, sync_call=sync_call,
        ))

        assert rc == 0
        assert sleeper.calls == [], "an explicit --quiet-for 0 must not wait at all"
        assert sync_call.calls == ["default"]

    def test_quiet_wait_clamps_a_backwards_clock_skew_to_the_quiet_window(self, env):
        """A stamp that reads in the FUTURE relative to `clock()` (a backwards
        wall-clock step) must not stretch the wait past `quiet_for` — the
        worker holds its own lock the whole time it waits."""
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        clock = FakeClock(start=1_000_000.0)
        publish.touch_request_stamp(vault, clock=lambda: clock.now + 10_000.0)

        def _settle_after_first_sleep(call_count, seconds):
            if call_count == 1:
                publish.touch_request_stamp(vault, clock=lambda: clock.now - 100.0)

        sleeper = RecordingSleeper(clock, on_sleep=_settle_after_first_sleep)

        publish._quiet_wait(vault, quiet_for=5.0, clock=clock, sleeper=sleeper)

        assert sleeper.calls, "a stamp reading in the future must still wait once"
        assert max(sleeper.calls) <= 5.0, (
            f"a backwards clock skew must not stretch the wait past quiet_for; "
            f"calls={sleeper.calls!r}"
        )


# ---------------------------------------------------------------------------
# retry rounds
# ---------------------------------------------------------------------------


class TestRetryRounds:
    def test_stamp_refreshed_during_sync_triggers_one_more_round(self, env):
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        clock = FakeClock()
        publish.touch_request_stamp(vault, clock=lambda: clock() - 10.0)
        sleeper = RecordingSleeper(clock)

        calls: list[str] = []

        def sync_call(vault_name):
            calls.append(vault_name)
            if len(calls) == 1:
                # A write lands while this round's sync is "in flight" — a
                # different stamp value, still outside the quiet window so
                # the retry round proceeds without waiting again.
                publish.touch_request_stamp(vault, clock=lambda: clock() - 6.0)
                return 0, "published"
            return 0, "converged"

        rc = publish.cmd_publish(SimpleNamespace(
            vault="default", quiet_for=5.0, clock=clock, sleeper=sleeper, sync_call=sync_call,
        ))

        assert rc == 0
        assert calls == ["default", "default"], "a stamp change during the sync must trigger exactly one retry"
        marker = publish.read_marker(vault)
        assert marker is None, "the terminal 'converged' outcome clears the marker"

    def test_in_progress_outcome_triggers_retry(self, env):
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        clock = FakeClock()
        publish.touch_request_stamp(vault, clock=lambda: clock() - 10.0)
        sleeper = RecordingSleeper(clock)

        outcomes = iter(["in-progress", "published"])

        def sync_call(vault_name):
            return 0, next(outcomes)

        rc = publish.cmd_publish(SimpleNamespace(
            vault="default", quiet_for=5.0, clock=clock, sleeper=sleeper, sync_call=sync_call,
        ))

        assert rc == 0
        assert next(outcomes, "exhausted") == "exhausted", "both stubbed outcomes must have been consumed"

    def test_rounds_exhausted_after_three_attempts(self, env):
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        clock = FakeClock()
        publish.touch_request_stamp(vault, clock=lambda: clock() - 10.0)
        sleeper = RecordingSleeper(clock)
        sync_call = _stub_sync_call("in-progress")

        rc = publish.cmd_publish(SimpleNamespace(
            vault="default", quiet_for=5.0, clock=clock, sleeper=sleeper, sync_call=sync_call,
        ))

        assert len(sync_call.calls) == 3, "the retry bound is 3 rounds, not unbounded"
        marker = publish.read_marker(vault)
        assert marker is not None
        assert marker["outcome"] == "rounds-exhausted"
        assert rc == 0, "the worker's exit code reflects the loop's own last rc"


# ---------------------------------------------------------------------------
# marker lifecycle
# ---------------------------------------------------------------------------


class TestMarkerLifecycle:
    def test_marker_written_running_before_sync_is_called_and_survives_a_raise(self, env):
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        clock = FakeClock()
        publish.touch_request_stamp(vault, clock=lambda: clock() - 10.0)
        sleeper = RecordingSleeper(clock)

        snapshot = {}

        def sync_call(vault_name):
            snapshot["marker"] = publish.read_marker(vault)
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            publish.cmd_publish(SimpleNamespace(
                vault="default", quiet_for=5.0, clock=clock, sleeper=sleeper, sync_call=sync_call,
            ))

        assert snapshot["marker"] is not None
        assert snapshot["marker"]["outcome"] == "running", (
            "the marker must be on disk with outcome 'running' before the sync is ever called"
        )
        assert snapshot["marker"]["pid"] == os.getpid()

        final_marker = publish.read_marker(vault)
        assert final_marker["outcome"] == "error", "the worker itself raising is reported as 'error'"

    def test_published_outcome_clears_the_marker(self, env):
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        clock = FakeClock()
        publish.touch_request_stamp(vault, clock=lambda: clock() - 10.0)
        sleeper = RecordingSleeper(clock)
        sync_call = _stub_sync_call("published")

        rc = publish.cmd_publish(SimpleNamespace(
            vault="default", quiet_for=5.0, clock=clock, sleeper=sleeper, sync_call=sync_call,
        ))

        assert rc == 0
        assert publish.read_marker(vault) is None

    def test_holding_outcome_leaves_marker_and_leaves_the_same_durable_failed_vault_marker(self, env):
        """Proven unknown: a bare-namespace `cmd_sync` sets/clears the sweep's own
        failed-vault marker via `resolve_state.mark_failed`/`clear_failed_marker`.
        The worker must add nothing on top of that — it neither writes nor
        clears that marker itself."""
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        clock = FakeClock()
        publish.touch_request_stamp(vault, clock=lambda: clock() - 10.0)
        sleeper = RecordingSleeper(clock)

        def sync_call(vault_name):
            resolve_state.mark_failed(vault, reason="policy-failure", detail="test induced")
            return 1, "holding"

        rc = publish.cmd_publish(SimpleNamespace(
            vault="default", quiet_for=5.0, clock=clock, sleeper=sleeper, sync_call=sync_call,
        ))

        assert rc == 1
        marker = publish.read_marker(vault)
        assert marker is not None
        assert marker["outcome"] == "holding"

        failed = resolve_state.read_failed_marker(vault)
        assert failed is not None
        assert failed["reason"] == "policy-failure"


# ---------------------------------------------------------------------------
# log file
# ---------------------------------------------------------------------------


class TestLogFile:
    def test_log_file_is_created_with_mode_0600(self, env):
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        clock = FakeClock()
        publish.touch_request_stamp(vault, clock=lambda: clock() - 10.0)
        sleeper = RecordingSleeper(clock)
        sync_call = _stub_sync_call("converged")

        publish.cmd_publish(SimpleNamespace(
            vault="default", quiet_for=5.0, clock=clock, sleeper=sleeper, sync_call=sync_call,
        ))

        log_file = publish.log_path(vault)
        assert log_file.exists()
        mode = os.stat(log_file).st_mode & 0o777
        assert mode == 0o600, f"log file mode was {oct(mode)}, not 0600"

    def test_log_file_pre_existing_with_looser_mode_is_tightened_to_0600(self, env):
        """POSIX applies ``O_CREAT``'s mode argument only when the ``open()``
        call actually creates the file — a log left over from before this
        worker existed (or written by something else) keeps whatever mode it
        already had. That log can carry sync stderr with a credentialed
        remote URL embedded in it, so reuse must tighten the mode, not trust
        the one already on disk."""
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        clock = FakeClock()
        publish.touch_request_stamp(vault, clock=lambda: clock() - 10.0)
        sleeper = RecordingSleeper(clock)
        sync_call = _stub_sync_call("converged")

        log_file = publish.log_path(vault)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("stale log content from a looser-mode era\n", encoding="utf-8")
        os.chmod(log_file, 0o644)

        publish.cmd_publish(SimpleNamespace(
            vault="default", quiet_for=5.0, clock=clock, sleeper=sleeper, sync_call=sync_call,
        ))

        mode = os.stat(log_file).st_mode & 0o777
        assert mode == 0o600, f"log file mode was {oct(mode)}, not 0600"


# ---------------------------------------------------------------------------
# marker / stamp / lock file modes — same pre-existing-file gap as the log
# ---------------------------------------------------------------------------


class TestReusedFileModesAreTightened:
    def test_write_marker_reuses_a_looser_mode_file_and_tightens_it(self, env):
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        path = publish.marker_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        os.chmod(path, 0o644)

        publish._write_marker(vault, {"outcome": "running"})

        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600, f"marker file mode was {oct(mode)}, not 0600"

    def test_touch_request_stamp_reuses_a_looser_mode_file_and_tightens_it(self, env):
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        path = publish.request_stamp_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("0", encoding="utf-8")
        os.chmod(path, 0o644)

        publish.touch_request_stamp(vault, clock=lambda: 42.0)

        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600, f"request stamp file mode was {oct(mode)}, not 0600"

    def test_open_lock_fd_reuses_a_looser_mode_file_and_tightens_it(self, env):
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        path = publish.lock_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        os.chmod(path, 0o644)

        fd = publish._open_lock_fd(vault)
        try:
            mode = os.stat(path).st_mode & 0o777
            assert mode == 0o600, f"lock file mode was {oct(mode)}, not 0600"
        finally:
            os.close(fd)


# ---------------------------------------------------------------------------
# single-flight
# ---------------------------------------------------------------------------


class TestSingleFlight:
    def test_second_worker_in_process_finds_the_lock_held_and_exits_zero(self, env, capsys):
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        clock = FakeClock()
        publish.touch_request_stamp(vault, clock=lambda: clock() - 10.0)

        # A distinct fd on the SAME lock path — a genuine second "open file
        # description" holding the flock, exactly as a second process would.
        lp = publish.lock_path(vault)
        lp.parent.mkdir(parents=True, exist_ok=True)
        holder_fd = os.open(lp, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            sync_call = _stub_sync_call("converged")
            rc = publish.cmd_publish(SimpleNamespace(
                vault="default", quiet_for=5.0, clock=clock,
                sleeper=RecordingSleeper(clock), sync_call=sync_call,
            ))
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)

        assert rc == 0
        assert sync_call.calls == [], "a held lock must run no sync at all"
        assert publish.read_marker(vault) is None, "a held lock must not even write the entry marker"
        assert "already in progress" in capsys.readouterr().err

    def test_second_worker_cross_process_flock_contention_exits_zero(self, env):
        """The real single-flight guarantee: a genuinely separate process
        holding the lock, mirroring `_spawn_holder` in test_vault_write_lock.py."""
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        publish.touch_request_stamp(vault, clock=lambda: time.time() - 10.0)

        lp = publish.lock_path(vault)
        lp.parent.mkdir(parents=True, exist_ok=True)
        holder_marker = env.tmp_path / "_held"
        holder_code = f"""
import fcntl, os, time
fd = os.open({str(lp)!r}, os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
open({str(holder_marker)!r}, "w").write("1")
time.sleep(1.0)
"""
        holder = subprocess.Popen([sys.executable, "-c", holder_code])
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not holder_marker.exists():
                time.sleep(0.005)
            assert holder_marker.exists(), "holder subprocess never acquired the lock"

            sync_call = _stub_sync_call("converged")
            clock = FakeClock()
            rc = publish.cmd_publish(SimpleNamespace(
                vault="default", quiet_for=5.0, clock=clock,
                sleeper=RecordingSleeper(clock), sync_call=sync_call,
            ))
        finally:
            holder.wait(timeout=15)

        assert rc == 0
        assert sync_call.calls == [], "a cross-process held lock must run no sync"

    def test_worker_for_a_different_vault_proceeds_while_another_is_held(self, env):
        held_vault = _make_vault(env.tmp_path, "held", config_home=env.config_home)
        free_vault = _make_vault(env.tmp_path, "free", config_home=env.config_home)
        clock = FakeClock()
        # Written against the SAME injected clock `cmd_publish` below reads
        # with — a stamp written against a different clock basis (e.g. real
        # `time.time()`) is a fresh-looking stamp from `_quiet_wait`'s clamped
        # point of view (`remaining` is bounded to `quiet_for` per read, so an
        # arbitrarily large, cross-basis gap would take arbitrarily many
        # iterations of this injected, non-blocking sleeper to close).
        publish.touch_request_stamp(free_vault, clock=lambda: clock() - 10.0)

        held_lock = publish.lock_path(held_vault)
        held_lock.parent.mkdir(parents=True, exist_ok=True)
        holder_fd = os.open(held_lock, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            sync_call = _stub_sync_call("converged")
            rc = publish.cmd_publish(SimpleNamespace(
                vault="free", quiet_for=5.0, clock=clock,
                sleeper=RecordingSleeper(clock), sync_call=sync_call,
            ))
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)

        assert rc == 0
        assert sync_call.calls == ["free"], "a different vault's worker must not be blocked"


# ---------------------------------------------------------------------------
# real wiring — no stubbed sync_call
# ---------------------------------------------------------------------------


class TestRealSyncWiring:
    def test_cmd_publish_runs_the_real_sync_and_publishes_a_pending_write(self, env):
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        remote = make_bare_remote(env.tmp_path / "remote.git")
        _wire_remote(vault, remote, track=True)

        (vault / "task" / "new-record.md").parent.mkdir(parents=True, exist_ok=True)
        (vault / "task" / "new-record.md").write_text("# pending\n", encoding="utf-8")

        clock = FakeClock()
        publish.touch_request_stamp(vault, clock=lambda: clock() - 10.0)

        rc = publish.cmd_publish(SimpleNamespace(
            vault="default", quiet_for=5.0, clock=clock, sleeper=RecordingSleeper(clock),
        ))

        assert rc == 0
        assert publish.read_marker(vault) is None, "a real successful publish clears the marker"
        clone = env.tmp_path / "clone-check"
        subprocess.run(["git", "clone", str(remote), str(clone)], check=True, capture_output=True)
        assert (clone / "task" / "new-record.md").exists(), (
            "the real cmd_sync path never pushed the pending write to origin"
        )

    def test_cmd_publish_never_blocks_on_a_contended_real_vault_lock(self, env):
        """A real, separate process holds the vault write lock (`_spawn_holder`,
        mirroring `test_vault_write_lock.py`'s own contention proof) while the
        worker runs the UNSTUBBED `_default_sync_call` against it. If the
        worker's `cmd_sync` invocation blocks (the default-`blocking=True`
        namespace bug), this test hangs for the holder's full `hold_for`
        instead of returning almost immediately with a contended-round
        outcome — proving the worker retries under real contention rather
        than stalling on the vault lock while holding its own single-flight
        lock."""
        vault = _make_vault(env.tmp_path, "default", config_home=env.config_home)
        remote = make_bare_remote(env.tmp_path / "remote.git")
        _wire_remote(vault, remote, track=True)

        clock = FakeClock()
        publish.touch_request_stamp(vault, clock=lambda: clock() - 10.0)

        holder = _spawn_holder(vault, hold_for=20.0)
        try:
            start = time.monotonic()
            rc = publish.cmd_publish(SimpleNamespace(
                vault="default", quiet_for=0.0, clock=clock, sleeper=RecordingSleeper(clock),
            ))
            elapsed = time.monotonic() - start
        finally:
            holder.kill()
            holder.wait(timeout=15)

        assert elapsed < 10.0, (
            f"worker blocked on the contended vault lock for {elapsed:.2f}s instead "
            "of retrying under blocking=False"
        )
        assert rc == 0
        marker = publish.read_marker(vault)
        assert marker is not None, "3 rounds under permanent contention leave a marker"
        assert marker["outcome"] == publish.OUTCOME_ROUNDS_EXHAUSTED, (
            f"expected rounds-exhausted under permanent contention; marker={marker!r}"
        )


# ---------------------------------------------------------------------------
# CLI parser wiring
# ---------------------------------------------------------------------------


class TestParserWiring:
    def test_publish_registers_required_vault_and_quiet_for(self, tmp_path):
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(tmp_path / "home"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "LORE_EMAIL": "tester@example.com",
        }
        missing_vault = subprocess.run(
            [sys.executable, str(CLI_PATH), "publish"],
            capture_output=True, text=True, env=env,
        )
        assert missing_vault.returncode == 2
        assert "--vault" in missing_vault.stderr

        help_result = subprocess.run(
            [sys.executable, str(CLI_PATH), "publish", "--help"],
            capture_output=True, text=True, env=env,
        )
        assert help_result.returncode == 0
        assert "--quiet-for" in help_result.stdout
