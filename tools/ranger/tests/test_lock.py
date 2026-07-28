"""Tests for ranger.sweep.lock — the one-sweep-per-vault mutex.

Test contract:
- acquire() creates the lock file 0600 with all four payload fields.
- A second acquire() while the recorded holder pid is alive raises LockError
  naming the holder's group/pid/host; the lock file is left untouched.
- A second acquire() whose recorded holder pid is dead raises LockError
  containing the exact `rm <absolute path>` removal command; file untouched.
- A corrupt/unreadable payload is treated the same as stale: reported for
  manual removal, never auto-deleted.
- Vault names containing separators, '..', or empty are refused before any
  filesystem access.
- release() removes a lock recorded under the caller's own pid; release()
  refuses a lock recorded under a different pid.
- All paths route through an injected `env` so tests never touch the real
  state dir.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "ranger" / "plugins" / "ranger"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from ranger.sweep import lock  # noqa: E402


def _env(tmp_path: Path) -> dict[str, str]:
    return {"RANGER_STATE_DIR": str(tmp_path / "state")}


def _dead_pid() -> int:
    """Return a pid guaranteed to be dead: spawn a trivial subprocess and reap it."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_acquire_creates_locked_file_with_payload(tmp_path):
    env = _env(tmp_path)

    path = lock.acquire("myvault", "mygroup", env=env)

    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600
    payload = json.loads(path.read_text())
    assert payload["group"] == "mygroup"
    assert payload["pid"] == os.getpid()
    assert payload["host"]
    assert payload["started_at"]


def test_acquire_refuses_when_holder_alive(tmp_path):
    env = _env(tmp_path)
    path = lock.acquire("myvault", "mygroup", env=env)
    before = path.read_text()

    with pytest.raises(lock.LockError) as exc_info:
        lock.acquire("myvault", "othergroup", env=env)

    message = str(exc_info.value)
    assert "mygroup" in message
    assert str(os.getpid()) in message
    assert path.read_text() == before


def test_acquire_reports_stale_when_holder_dead(tmp_path):
    env = _env(tmp_path)
    dead_pid = _dead_pid()
    path = lock.lock_path("myvault", env=env)
    path.parent.mkdir(parents=True)
    payload = {"group": "gonegroup", "pid": dead_pid, "host": "somehost", "started_at": "x"}
    path.write_text(json.dumps(payload))
    before = path.read_text()

    with pytest.raises(lock.LockError) as exc_info:
        lock.acquire("myvault", "newgroup", env=env)

    assert f"rm {path}" in str(exc_info.value)
    assert path.read_text() == before


def test_acquire_reports_stale_for_corrupt_payload(tmp_path):
    env = _env(tmp_path)
    path = lock.lock_path("myvault", env=env)
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    before = path.read_text()

    with pytest.raises(lock.LockError) as exc_info:
        lock.acquire("myvault", "newgroup", env=env)

    assert f"rm {path}" in str(exc_info.value)
    assert path.read_text() == before


@pytest.mark.parametrize("bad_name", ["../x", "a/b", ""])
def test_acquire_refuses_bad_vault_name_before_filesystem_access(tmp_path, bad_name):
    env = _env(tmp_path)

    with pytest.raises(lock.LockError):
        lock.acquire(bad_name, "group", env=env)

    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("bad_name", ["../x", "a/b", ""])
def test_lock_path_refuses_bad_vault_name(tmp_path, bad_name):
    env = _env(tmp_path)

    with pytest.raises(lock.LockError):
        lock.lock_path(bad_name, env=env)


def test_release_removes_own_lock(tmp_path):
    env = _env(tmp_path)
    path = lock.acquire("myvault", "mygroup", env=env)

    lock.release("myvault", env=env)

    assert not path.exists()


def test_release_refuses_lock_held_by_different_pid(tmp_path):
    env = _env(tmp_path)
    path = lock.lock_path("myvault", env=env)
    path.parent.mkdir(parents=True)
    payload = {"group": "othergroup", "pid": os.getpid() + 12345, "host": "h", "started_at": "x"}
    path.write_text(json.dumps(payload))

    with pytest.raises(lock.LockError):
        lock.release("myvault", env=env)

    assert path.exists()
