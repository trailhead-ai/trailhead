"""Tests for the workspace session lockfile (refuse-concurrent).

Behavioral coverage that replaces the earlier PID-liveness assumption probe.
The lockfile records a live PID + a session-start
timestamp; a lock held by a LIVE PID refuses (naming workspace + PID +
timestamp), a stale lock (dead PID) is reclaimed, and an age-bound fallback
reclaim covers PID recycling (psutil is absent → no create_time comparison).

Covers (lock portion):
- is_pid_alive: dead PID → False; self/live PID → True; PermissionError → True.
- acquire writes {pid, started_at, workspace} with the current PID + UTC timestamp.
- acquire against a LIVE-PID lock → SessionLockHeld naming workspace + PID + timestamp;
  lock NOT overwritten.
- acquire against a DEAD-PID lock → reclaimed, new lock written.
- acquire against a LIVE-PID lock older than the age threshold → reclaimed
  (PID-recycling fallback).
- release clears the lock.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _dead_pid() -> int:
    """Spawn + reap a child so its PID is (almost certainly) dead."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid = child.pid
    child.wait()
    return pid


# ---------------------------------------------------------------------------
# is_pid_alive
# ---------------------------------------------------------------------------


class TestIsPidAlive:
    def test_self_pid_is_alive(self):
        from session_lock import is_pid_alive

        assert is_pid_alive(os.getpid()) is True

    def test_dead_pid_is_not_alive(self):
        from session_lock import is_pid_alive

        pid = _dead_pid()
        if is_pid_alive(pid):
            pytest.skip("PID recycled immediately after reap")
        assert is_pid_alive(pid) is False

    def test_live_child_is_alive(self):
        from session_lock import is_pid_alive

        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            assert is_pid_alive(child.pid) is True
        finally:
            child.terminate()
            child.wait()

    def test_permission_error_treated_as_alive(self, monkeypatch):
        import session_lock

        def raise_eperm(pid, sig):
            raise PermissionError("EPERM")

        monkeypatch.setattr(session_lock.os, "kill", raise_eperm)
        assert session_lock.is_pid_alive(12345) is True


# ---------------------------------------------------------------------------
# acquire / release lifecycle
# ---------------------------------------------------------------------------


class TestAcquireRelease:
    def test_acquire_writes_lock_with_pid_and_timestamp(self, tmp_path):
        from session_lock import acquire_session_lock, lock_path_for

        ws = tmp_path / "ws"
        ws.mkdir()
        acquire_session_lock(ws)

        data = json.loads(lock_path_for(ws).read_text())
        assert data["pid"] == os.getpid()
        assert data["workspace"] == str(ws)
        # started_at parses as an ISO8601 UTC timestamp.
        parsed = datetime.fromisoformat(data["started_at"])
        assert parsed.tzinfo is not None

    def test_release_clears_lock(self, tmp_path):
        from session_lock import acquire_session_lock, release_session_lock, lock_path_for

        ws = tmp_path / "ws"
        ws.mkdir()
        acquire_session_lock(ws)
        assert lock_path_for(ws).exists()

        release_session_lock(ws)
        assert not lock_path_for(ws).exists()

    def test_release_is_idempotent(self, tmp_path):
        from session_lock import release_session_lock

        ws = tmp_path / "ws"
        ws.mkdir()
        # No lock present — release must not raise.
        release_session_lock(ws)


# ---------------------------------------------------------------------------
# refuse-concurrent / reclaim
# ---------------------------------------------------------------------------


class TestRefuseAndReclaim:
    def _write_lock(self, ws, pid, started_at):
        from session_lock import lock_path_for

        lock_path_for(ws).write_text(
            json.dumps({"pid": pid, "started_at": started_at, "workspace": str(ws)})
        )

    def test_live_pid_lock_refuses(self, tmp_path):
        from session_lock import acquire_session_lock, SessionLockHeld

        ws = tmp_path / "ws"
        ws.mkdir()
        now = datetime.now(timezone.utc)
        self._write_lock(ws, os.getpid(), now.isoformat())

        with pytest.raises(SessionLockHeld):
            acquire_session_lock(ws)

    def test_live_pid_refusal_names_workspace_pid_timestamp(self, tmp_path):
        from session_lock import acquire_session_lock, SessionLockHeld

        ws = tmp_path / "ws"
        ws.mkdir()
        ts = datetime.now(timezone.utc).isoformat()
        self._write_lock(ws, os.getpid(), ts)

        with pytest.raises(SessionLockHeld) as exc:
            acquire_session_lock(ws)
        msg = str(exc.value)
        assert str(ws) in msg
        assert str(os.getpid()) in msg
        assert ts in msg

    def test_live_pid_lock_not_overwritten_on_refusal(self, tmp_path):
        from session_lock import acquire_session_lock, SessionLockHeld, lock_path_for

        ws = tmp_path / "ws"
        ws.mkdir()
        ts = datetime.now(timezone.utc).isoformat()
        self._write_lock(ws, os.getpid(), ts)
        before = lock_path_for(ws).read_text()

        with pytest.raises(SessionLockHeld):
            acquire_session_lock(ws)
        assert lock_path_for(ws).read_text() == before

    def test_dead_pid_lock_is_reclaimed(self, tmp_path):
        from session_lock import acquire_session_lock, lock_path_for

        ws = tmp_path / "ws"
        ws.mkdir()
        pid = _dead_pid()
        from session_lock import is_pid_alive

        if is_pid_alive(pid):
            pytest.skip("PID recycled immediately after reap")
        self._write_lock(ws, pid, datetime.now(timezone.utc).isoformat())

        acquire_session_lock(ws)
        data = json.loads(lock_path_for(ws).read_text())
        assert data["pid"] == os.getpid()

    def test_old_live_lock_reclaimed_via_age_fallback(self, tmp_path):
        """A LIVE-PID lock older than the age threshold is reclaimed — the
        PID-recycling fallback (psutil absent → cannot compare create_time)."""
        from session_lock import acquire_session_lock, lock_path_for, STALE_AFTER_SECONDS

        ws = tmp_path / "ws"
        ws.mkdir()
        old = datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_SECONDS + 60)
        # Use our own (live) PID so liveness is true; only age makes it reclaimable.
        self._write_lock(ws, os.getpid(), old.isoformat())

        acquire_session_lock(ws)
        data = json.loads(lock_path_for(ws).read_text())
        # Reclaimed → fresh timestamp, our PID.
        assert data["pid"] == os.getpid()
        parsed = datetime.fromisoformat(data["started_at"])
        assert parsed > old

    def test_fresh_live_lock_within_threshold_refuses(self, tmp_path):
        from session_lock import acquire_session_lock, SessionLockHeld, STALE_AFTER_SECONDS

        ws = tmp_path / "ws"
        ws.mkdir()
        recent = datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_SECONDS - 60)
        self._write_lock(ws, os.getpid(), recent.isoformat())

        with pytest.raises(SessionLockHeld):
            acquire_session_lock(ws)

    def test_corrupt_lock_is_reclaimed(self, tmp_path):
        """An unparseable lockfile is treated as stale and reclaimed."""
        from session_lock import acquire_session_lock, lock_path_for

        ws = tmp_path / "ws"
        ws.mkdir()
        lock_path_for(ws).write_text("not json {{{")

        acquire_session_lock(ws)
        data = json.loads(lock_path_for(ws).read_text())
        assert data["pid"] == os.getpid()
