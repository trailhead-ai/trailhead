"""Tests for ranger.sweep.lock — the one-sweep-per-vault mutex.

Test contract:
- acquire() creates the lock file 0600 with all five payload fields, and
  records the *supplied* holder pid rather than its own — a sweep outlives the
  process that acquires its lock, so recording the acquirer would make every
  live sweep read as stale.
- acquire() refuses a holder pid that isn't a positive process id.
- A second acquire() while the recorded holder pid is alive raises LockError
  naming the holder's group/pid/host; the lock file is left untouched.
- A second acquire() whose recorded holder pid is dead raises LockError
  containing the exact `rm <absolute path>` removal command; file untouched.
- A corrupt/unreadable payload is treated the same as stale: reported for
  manual removal, never auto-deleted.
- Vault names containing separators, '..', or empty are refused before any
  filesystem access.
- release() removes the lock for a caller presenting the token acquire()
  returned, and refuses every other caller — a token from a different run, an
  unreadable payload, or no lock at all — leaving the file untouched.
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

    path, token = lock.acquire("myvault", "mygroup", holder_pid=os.getpid(), env=env)

    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600
    payload = json.loads(path.read_text())
    assert payload["group"] == "mygroup"
    assert payload["pid"] == os.getpid()
    assert payload["host"]
    assert payload["started_at"]
    assert payload["token"] == token
    assert len(token) >= 32, "the token must be long enough not to be guessable"


def test_acquire_records_the_supplied_holder_pid_not_its_own(tmp_path):
    """The holder is the long-lived sweep, not whatever process acquires the
    lock — recording the caller's own pid would make a live sweep read stale."""
    env = _env(tmp_path)
    holder = _dead_pid()  # any pid that is not this process's

    path, _token = lock.acquire("myvault", "mygroup", holder_pid=holder, env=env)

    assert json.loads(path.read_text())["pid"] == holder


@pytest.mark.parametrize("bad_pid", [0, -1, "1234"])
def test_acquire_refuses_a_non_pid_holder(tmp_path, bad_pid):
    env = _env(tmp_path)

    with pytest.raises(lock.LockError):
        lock.acquire("myvault", "mygroup", holder_pid=bad_pid, env=env)

    assert not lock.lock_path("myvault", env=env).exists()


def test_acquire_refuses_when_holder_alive(tmp_path):
    env = _env(tmp_path)
    path, _token = lock.acquire("myvault", "mygroup", holder_pid=os.getpid(), env=env)
    before = path.read_text()

    with pytest.raises(lock.LockError) as exc_info:
        lock.acquire("myvault", "othergroup", holder_pid=os.getpid(), env=env)

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
        lock.acquire("myvault", "newgroup", holder_pid=os.getpid(), env=env)

    assert f"rm {path}" in str(exc_info.value)
    assert path.read_text() == before


def test_acquire_reports_stale_for_corrupt_payload(tmp_path):
    env = _env(tmp_path)
    path = lock.lock_path("myvault", env=env)
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    before = path.read_text()

    with pytest.raises(lock.LockError) as exc_info:
        lock.acquire("myvault", "newgroup", holder_pid=os.getpid(), env=env)

    assert f"rm {path}" in str(exc_info.value)
    assert path.read_text() == before


@pytest.mark.parametrize("bad_name", ["../x", "a/b", ""])
def test_acquire_refuses_bad_vault_name_before_filesystem_access(tmp_path, bad_name):
    env = _env(tmp_path)

    with pytest.raises(lock.LockError):
        lock.acquire(bad_name, "group", holder_pid=os.getpid(), env=env)

    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("bad_name", ["../x", "a/b", ""])
def test_lock_path_refuses_bad_vault_name(tmp_path, bad_name):
    env = _env(tmp_path)

    with pytest.raises(lock.LockError):
        lock.lock_path(bad_name, env=env)


def test_release_removes_the_lock_for_a_matching_token(tmp_path):
    env = _env(tmp_path)
    path, token = lock.acquire("myvault", "mygroup", holder_pid=os.getpid(), env=env)

    lock.release("myvault", token=token, env=env)

    assert not path.exists()


def test_release_refuses_a_token_from_a_different_run(tmp_path):
    """A release is authorized by the run that took the lock or not at all —
    a later process holds no pid or path evidence that could substitute."""
    env = _env(tmp_path)
    path, _token = lock.acquire("myvault", "mygroup", holder_pid=os.getpid(), env=env)
    before = path.read_text()

    with pytest.raises(lock.LockError):
        lock.release("myvault", token="0" * 32, env=env)

    assert path.exists()
    assert path.read_text() == before


def test_release_refuses_a_lock_with_an_unreadable_payload(tmp_path):
    env = _env(tmp_path)
    path = lock.lock_path("myvault", env=env)
    path.parent.mkdir(parents=True)
    path.write_text("not json")

    with pytest.raises(lock.LockError):
        lock.release("myvault", token="0" * 32, env=env)

    assert path.exists()


def test_release_refuses_when_there_is_no_lock(tmp_path):
    env = _env(tmp_path)

    with pytest.raises(lock.LockError):
        lock.release("myvault", token="0" * 32, env=env)
