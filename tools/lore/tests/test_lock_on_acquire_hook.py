"""The on-acquire hook ``locking`` runs for a fresh vault-scope acquisition.

Recovery of an interrupted pair write rides this hook, so its firing rules are
what decide whether every lore writer recovers exactly once per vault.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest


from lore import locking
from test_record_pair_write import (
    AMENDED,
    BODY,
    _crash_pair,
    _journals,
)


@pytest.fixture
def events(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(locking, "_on_acquire", [lambda root: seen.append(Path(root).name)])
    return seen


@pytest.fixture
def vaults(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    return a, b


def test_hook_fires_once_for_an_outermost_acquisition_not_for_nesting(events, vaults):
    a, _ = vaults
    with locking.vault_write_lock(a):
        with locking.vault_write_lock(a):
            with locking.vault_write_lock(a):
                pass
    assert events == ["a"]


def test_hook_fires_again_after_release(events, vaults):
    a, _ = vaults
    with locking.vault_write_lock(a):
        pass
    with locking.vault_write_lock(a):
        pass
    assert events == ["a", "a"]


def test_hook_does_not_fire_for_session_scope(events, vaults, tmp_path):
    a, _ = vaults
    with locking.session_write_lock(a, "key", env={"XDG_STATE_HOME": str(tmp_path / "st")}):
        pass
    assert events == []


def test_hook_does_not_fire_when_a_nonblocking_acquire_loses_to_contention(events, vaults):
    a, _ = vaults
    held, release = threading.Event(), threading.Event()

    def holder():
        with locking.vault_write_lock(a):
            held.set()
            release.wait(10)

    t = threading.Thread(target=holder)
    t.start()
    assert held.wait(10)
    events.clear()
    try:
        with pytest.raises(BlockingIOError):
            with locking.vault_write_lock(a, blocking=False):
                pass
    finally:
        release.set()
        t.join()
    assert events == []


def test_hook_fires_once_per_vault_in_sorted_order(events, vaults):
    a, b = vaults
    with locking.vault_write_locks(b, a):
        pass
    assert events == ["a", "b"]


def test_a_raising_hook_releases_the_lock(monkeypatch, vaults):
    a, _ = vaults

    def boom(root):
        raise RuntimeError("hook failed")

    monkeypatch.setattr(locking, "_on_acquire", [boom])
    with pytest.raises(RuntimeError):
        with locking.vault_write_lock(a):
            pytest.fail("body must not run")
    monkeypatch.setattr(locking, "_on_acquire", [])
    with locking.vault_write_lock(a, blocking=False):
        pass


def test_vault_write_locks_recovers_every_vault_holding_a_journal(tmp_path):
    state = tmp_path / "state"
    roots = []
    for name in ("a", "b"):
        root = tmp_path / name
        (root / "decision").mkdir(parents=True)
        body, side = root / "decision" / "x.md", root / "decision" / "x.json"
        body.write_text(BODY)
        side.write_text("{}\n")
        _crash_pair(root, state, "decision/x", AMENDED, '{"changed": true}\n')
        assert body.read_text() == AMENDED and side.read_text() == "{}\n"
        assert len(_journals(state, root)) == 1
        roots.append(root)

    with locking.vault_write_locks(*roots):
        pass

    for root in roots:
        assert (root / "decision" / "x.md").read_text() == BODY
        assert (root / "decision" / "x.json").read_text() == "{}\n"
        assert _journals(state, root) == []
