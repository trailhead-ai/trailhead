"""Tests for transfer/conversations.py — workspace-scoped conversation enumeration.

Test contract:
- A conversation whose recorded root is the workspace root is reported at the
  root.
- A conversation rooted in a member subdirectory is reported at that relative
  subpath, not at the root.
- A conversation whose recorded root is outside the workspace is excluded.
- A conversation with no readable recorded root is reported as unresolved, and
  is distinguishable from both "rooted here" and "excluded".
- A live conversation is reported live; a stopped one is reported stopped.
- A workspace with no conversations rooted in it returns an empty result,
  distinguishable from a failure to read.
- The same conversation appearing in both pools yields exactly one row.
- An unreadable half of the pool propagates as the fail-closed enumeration
  error rather than as an empty or partial result.
- The enumeration writes nothing and starts no process of its own, asserted by
  a byte-identical snapshot of the whole camp state directory across the call.

Every path comes from ``tmp_path`` and every group state dir from an injected
``CAMP_STATE_DIR``, so no test reads the operator's real state.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

_NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)

_UUID_A = "aaaaaaaa-1111-4111-8111-111111111111"
_UUID_B = "bbbbbbbb-2222-4222-8222-222222222222"


def _env(state_root: Path) -> dict[str, str]:
    return {"CAMP_STATE_DIR": str(state_root), "HOME": str(state_root.parent / "home")}


def _group(name: str) -> dict[str, Any]:
    return {"group": {"name": name}}


def _workspace(state_root: Path, group: str, slug: str) -> Path:
    ws = state_root / group / "worktrees" / slug
    ws.mkdir(parents=True)
    return ws


def _transcript(session_id: str, cwd: Path | None, *, age_seconds: float = 60.0):
    from trailhead.harness.base import SessionTranscript

    return SessionTranscript(
        session_id=session_id,
        cwd=cwd,
        modified_at=_NOW - timedelta(seconds=age_seconds),
    )


def _record(session_id: str, cwd: Path):
    from trailhead.harness.base import SessionRecord

    return SessionRecord(
        session_id=session_id,
        cwd=cwd,
        kind="agent",
        controllable=True,
        name=None,
        pid=None,
        started_at=None,
    )


def _rows(workspace: Path, *, transcripts, live_records, groups, env):
    from camp.transfer.conversations import workspace_conversations

    return workspace_conversations(
        workspace,
        transcripts=transcripts,
        live_records=live_records,
        groups=groups,
        env=env,
        now=_NOW,
    )


# ---------------------------------------------------------------------------
# Where a conversation is reported
# ---------------------------------------------------------------------------


def test_conversation_rooted_at_workspace_root_reported_at_root(tmp_path: Path) -> None:
    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    rows = _rows(
        ws,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[],
        groups=[_group("g")],
        env=env,
    )
    assert len(rows) == 1
    assert rows[0].session_id == _UUID_A
    assert rows[0].subpath == PurePosixPath(".")
    assert rows[0].unresolved is False


def test_conversation_rooted_in_member_subdir_reported_at_relative_subpath(
    tmp_path: Path,
) -> None:
    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    member = ws / "repo_a"
    member.mkdir()
    rows = _rows(
        ws,
        transcripts=[_transcript(_UUID_A, member)],
        live_records=[],
        groups=[_group("g")],
        env=env,
    )
    assert len(rows) == 1
    assert rows[0].subpath == PurePosixPath("repo_a")


def test_conversation_rooted_outside_workspace_excluded(tmp_path: Path) -> None:
    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    rows = _rows(
        ws,
        transcripts=[_transcript(_UUID_A, elsewhere)],
        live_records=[],
        groups=[_group("g")],
        env=env,
    )
    assert rows == ()


def test_conversation_with_unreadable_root_reported_unresolved(tmp_path: Path) -> None:
    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    rows = _rows(
        ws,
        transcripts=[_transcript(_UUID_A, None)],
        live_records=[],
        groups=[_group("g")],
        env=env,
    )
    assert len(rows) == 1
    assert rows[0].unresolved is True
    assert rows[0].subpath is None
    # Distinguishable from "rooted here" (subpath would be PurePosixPath(".")).
    assert rows[0].subpath != PurePosixPath(".")


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------


def test_live_conversation_reported_live(tmp_path: Path) -> None:
    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    rows = _rows(
        ws,
        transcripts=[],
        live_records=[_record(_UUID_A, ws)],
        groups=[_group("g")],
        env=env,
    )
    assert len(rows) == 1
    assert rows[0].live is True


def test_stopped_conversation_reported_stopped(tmp_path: Path) -> None:
    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    rows = _rows(
        ws,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[],
        groups=[_group("g")],
        env=env,
    )
    assert len(rows) == 1
    assert rows[0].live is False


# ---------------------------------------------------------------------------
# Empty result vs. failure to read
# ---------------------------------------------------------------------------


def test_no_conversations_returns_empty_result(tmp_path: Path) -> None:
    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    rows = _rows(ws, transcripts=[], live_records=[], groups=[_group("g")], env=env)
    assert rows == ()


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


def test_same_conversation_in_both_pools_yields_one_row(tmp_path: Path) -> None:
    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    rows = _rows(
        ws,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        groups=[_group("g")],
        env=env,
    )
    assert len(rows) == 1
    assert rows[0].session_id == _UUID_A
    assert rows[0].live is True


# ---------------------------------------------------------------------------
# Fail-closed
# ---------------------------------------------------------------------------


def test_unenumerable_transcripts_fail_closed(tmp_path: Path) -> None:
    from camp.launch.teardown_guard import EnumerationUnavailable

    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    with pytest.raises(EnumerationUnavailable):
        _rows(ws, transcripts=None, live_records=[], groups=[_group("g")], env=env)


def test_unenumerable_live_records_fail_closed(tmp_path: Path) -> None:
    from camp.launch.teardown_guard import EnumerationUnavailable

    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    with pytest.raises(EnumerationUnavailable):
        _rows(ws, transcripts=[], live_records=None, groups=[_group("g")], env=env)


# ---------------------------------------------------------------------------
# Statelessness — seam smoke
# ---------------------------------------------------------------------------


def _snapshot(root: Path) -> dict[str, tuple[str, object]]:
    """Every path under *root*, with its kind and its full content.

    Mirrors `test_statelessness.py`'s `_snapshot`: directories carry no
    content, symlinks carry their unresolved target, and regular files carry
    their bytes, so a rewrite-in-place is visible even when paths don't change.
    """
    snapshot: dict[str, tuple[str, object]] = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        if path.is_symlink():
            snapshot[key] = ("symlink", os.readlink(path))
        elif path.is_dir():
            snapshot[key] = ("dir", None)
        else:
            snapshot[key] = ("file", path.read_bytes())
    return snapshot


# A seam pin, not a behavioural assertion: the enumeration is a pure function over
# injected data, so the only thing a state snapshot can show is that the seam reaches
# no store of its own. It needs no inert-gate exemption — it does run its subject.
def test_enumeration_writes_nothing_to_camp_state(tmp_path: Path) -> None:
    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    state_root = Path(env["CAMP_STATE_DIR"])
    before = _snapshot(state_root)
    _rows(
        ws,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_B, ws)],
        groups=[_group("g")],
        env=env,
    )
    after = _snapshot(state_root)
    assert before == after
