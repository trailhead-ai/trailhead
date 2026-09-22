"""Tests for `group/manifest.py`'s bounded-acquire mode on `reconcile_lock`.

Test contract:

1. `reconcile_lock(ws_dir, timeout=<n>)` against a lock already held (by
   another thread in this process — flock is per open file description, so
   this is the same contention a separate process would see) raises
   `LockTimeout` within the bound, rather than blocking forever.
2. `reconcile_lock(ws_dir)` with no `timeout` (the default) keeps today's
   unbounded behaviour: it blocks until the holder releases.

Mirrors test_window_reconcile.py's convention: sys.path is set up for the
plugin package before any `camp.*` import, and every `camp.*` symbol is
imported inside the function that uses it.
"""

from __future__ import annotations

import fcntl
import os
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def test_bounded_acquire_against_a_held_lock_raises_within_the_bound(tmp_path: Path) -> None:
    from camp.group.manifest import LockTimeout, reconcile_lock

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    result: dict[str, object] = {}
    holder_ready = threading.Event()
    release_holder = threading.Event()

    def hold() -> None:
        with reconcile_lock(ws_dir):
            holder_ready.set()
            release_holder.wait(timeout=5)

    def try_bounded() -> None:
        start = time.monotonic()
        try:
            with reconcile_lock(ws_dir, timeout=0.2):
                result["acquired"] = True
        except LockTimeout as exc:
            result["elapsed"] = time.monotonic() - start
            result["error"] = exc

    holder = threading.Thread(target=hold)
    holder.start()
    assert holder_ready.wait(timeout=5)

    waiter = threading.Thread(target=try_bounded)
    waiter.start()
    waiter.join(timeout=5)

    release_holder.set()
    holder.join(timeout=5)

    assert "error" in result, result
    assert isinstance(result["error"], LockTimeout)
    assert result["elapsed"] < 2.0


def test_unbounded_acquire_still_blocks_until_the_holder_releases(tmp_path: Path) -> None:
    from camp.group.manifest import reconcile_lock

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    acquired = threading.Event()
    release_holder = threading.Event()
    second_done = threading.Event()

    def hold() -> None:
        with reconcile_lock(ws_dir):
            acquired.set()
            release_holder.wait(timeout=5)

    def second() -> None:
        with reconcile_lock(ws_dir):
            second_done.set()

    holder = threading.Thread(target=hold)
    holder.start()
    assert acquired.wait(timeout=5)

    waiter = threading.Thread(target=second)
    waiter.start()

    still_blocked = not second_done.wait(timeout=0.3)
    assert still_blocked, "an unbounded acquire proceeded while the lock was held"

    release_holder.set()
    holder.join(timeout=5)
    waiter.join(timeout=5)
    assert second_done.is_set()


def test_the_bound_is_one_deadline_across_a_reaped_lockfile_retry(tmp_path: Path, monkeypatch) -> None:
    """A lockfile reaped between the flock and the inode re-check makes the
    acquire retry on a fresh file; the retry spends what is left of the same
    budget, never a fresh one. The clock, the flock, and the stat are all
    scripted so the sequence is exact: contended until 0.6s, one acquire
    that lands on a reaped inode, then contended for good."""
    import types

    from camp.group import manifest
    from camp.group.manifest import LockTimeout, lock_path_for, reconcile_lock

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    lock_path = lock_path_for(ws_dir)

    clock = {"now": 0.0}
    state = {"reaped": False}

    def fake_sleep(seconds: float) -> None:
        clock["now"] += seconds

    def fake_flock(fd: int, op: int) -> None:
        if op & fcntl.LOCK_UN:
            return
        if not state["reaped"] and clock["now"] >= 0.6:
            return  # the first holder let go; this acquire lands on its reaped inode
        raise BlockingIOError

    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        if not state["reaped"] and str(path) == str(lock_path):
            state["reaped"] = True
            raise FileNotFoundError(path)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(manifest, "time", types.SimpleNamespace(monotonic=lambda: clock["now"], sleep=fake_sleep))
    monkeypatch.setattr(manifest, "os", types.SimpleNamespace(stat=fake_stat, fstat=os.fstat))
    monkeypatch.setattr(
        manifest,
        "fcntl",
        types.SimpleNamespace(flock=fake_flock, LOCK_EX=fcntl.LOCK_EX, LOCK_NB=fcntl.LOCK_NB, LOCK_UN=fcntl.LOCK_UN),
    )

    with pytest.raises(LockTimeout):
        with reconcile_lock(ws_dir, timeout=1.0):
            pass

    assert state["reaped"] is True
    assert clock["now"] < 1.2, clock["now"]
