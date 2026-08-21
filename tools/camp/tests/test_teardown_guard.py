"""Tests for launch/teardown_guard.py — the derived `camp rm` session guard.

Test contract:
- A live session rooted in the workspace blocks removal; a recoverable
  (parked, not live) one blocks it too — a parked session is exactly the case
  the guard exists for.
- The rule is a SUBTREE rule: a session rooted BELOW the workspace directory
  blocks it, not only one rooted exactly at it.
- A session rooted anywhere else — including in a sibling workspace — blocks
  nothing, and a workspace with no sessions at all is clear.
- A pool camp could not enumerate raises `EnumerationUnavailable` rather than
  answering "nothing blocks": removal is destructive and irreversible, so an
  unanswerable seam is never a permissive default.
- The refusal names every blocking session by derived name and by root, so the
  operator can stop it, resume it, or re-run with `--force`.
- The live probe is tri-state at its own seam: a harness with no enumeration
  concept, or one whose probe times out, is UNKNOWN and fails closed; a probe
  that ran and answered — including one whose binary is not installed, under
  which nothing can be running — is an answer.
- The module stays pure data-to-data: no printing, no exiting, no `os.environ`.

Every path comes from ``tmp_path`` and every group state dir from an injected
``CAMP_STATE_DIR``, so no test reads the operator's real state.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


def _blocking(workspace: Path, *, transcripts, live_records, groups, env):
    from camp.launch.teardown_guard import blocking_sessions

    return blocking_sessions(
        workspace,
        transcripts=transcripts,
        live_records=live_records,
        groups=groups,
        env=env,
        now=_NOW,
    )


# ---------------------------------------------------------------------------
# What blocks
# ---------------------------------------------------------------------------


def test_a_live_session_rooted_in_the_workspace_blocks(tmp_path: Path) -> None:
    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    blocking = _blocking(
        ws,
        transcripts=[],
        live_records=[_record(_UUID_A, ws)],
        groups=[_group("g")],
        env=env,
    )
    assert [c.session_id for c in blocking] == [_UUID_A]
    assert blocking[0].live is True


def test_a_recoverable_session_rooted_in_the_workspace_blocks(tmp_path: Path) -> None:
    """A parked session is exactly the case this guard exists for."""
    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    blocking = _blocking(
        ws,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[],
        groups=[_group("g")],
        env=env,
    )
    assert [c.session_id for c in blocking] == [_UUID_A]
    assert blocking[0].live is False


def test_a_session_rooted_below_the_workspace_blocks(tmp_path: Path) -> None:
    """The subtree rule: a group's harness cwd routinely roots below the
    workspace, so an equality test would miss the common case."""
    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    member = ws / "repo_a"
    member.mkdir()
    blocking = _blocking(
        ws,
        transcripts=[_transcript(_UUID_A, member)],
        live_records=[],
        groups=[_group("g")],
        env=env,
    )
    assert [c.session_id for c in blocking] == [_UUID_A]


def test_a_session_rooted_elsewhere_does_not_block(tmp_path: Path) -> None:
    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    other = _workspace(tmp_path, "g", "other-ws")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    blocking = _blocking(
        ws,
        transcripts=[_transcript(_UUID_A, other), _transcript(_UUID_B, elsewhere)],
        live_records=[],
        groups=[_group("g")],
        env=env,
    )
    assert blocking == ()


def test_an_empty_pool_blocks_nothing(tmp_path: Path) -> None:
    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    assert _blocking(ws, transcripts=[], live_records=[], groups=[_group("g")], env=env) == ()


def test_a_session_whose_root_is_gone_does_not_block(tmp_path: Path) -> None:
    """A root that no longer exists cannot be resumed there, and an entry left
    behind by an interrupted teardown must not wedge the re-attempt."""
    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    gone = ws / "already-removed"
    blocking = _blocking(
        ws,
        transcripts=[_transcript(_UUID_A, gone)],
        live_records=[],
        groups=[_group("g")],
        env=env,
    )
    assert blocking == ()


def test_one_session_in_both_pools_blocks_once(tmp_path: Path) -> None:
    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    blocking = _blocking(
        ws,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        groups=[_group("g")],
        env=env,
    )
    assert len(blocking) == 1
    assert blocking[0].live is True


# ---------------------------------------------------------------------------
# Fail-closed
# ---------------------------------------------------------------------------


def test_unenumerable_transcripts_fail_closed(tmp_path: Path) -> None:
    from camp.launch.teardown_guard import EnumerationUnavailable

    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    with pytest.raises(EnumerationUnavailable):
        _blocking(ws, transcripts=None, live_records=[], groups=[_group("g")], env=env)


def test_unenumerable_live_sessions_fail_closed(tmp_path: Path) -> None:
    from camp.launch.teardown_guard import EnumerationUnavailable

    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    with pytest.raises(EnumerationUnavailable):
        _blocking(ws, transcripts=[], live_records=None, groups=[_group("g")], env=env)


# ---------------------------------------------------------------------------
# The pool gatherer's tri-state live probe
# ---------------------------------------------------------------------------


class _Harness:
    """A harness stand-in whose two seams are scripted per test."""

    def __init__(self, *, transcripts=(), enumerate_argv=("probe",), records=()):
        self._transcripts = transcripts
        self._enumerate_argv = enumerate_argv
        self._records = records

    def session_transcripts(self, workspace=None, *, env=None):
        if self._transcripts is None:
            raise RuntimeError("unreadable store")
        return list(self._transcripts)

    def session_enumerate(self, workspace=None):
        return list(self._enumerate_argv) if self._enumerate_argv else []

    def parse_session_list(self, output):
        return list(self._records)


def _gather(harnesses, env, monkeypatch, *, run=None):
    import camp.launch.teardown_guard as guard

    if run is not None:
        monkeypatch.setattr(guard.subprocess, "run", run)
    return guard.gather_pool(harnesses, env=env)


def _ok(stdout: str = "[]"):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    return run


def test_gather_pool_returns_both_halves(tmp_path: Path, monkeypatch) -> None:
    ws = _workspace(tmp_path, "g", "ws")
    harness = _Harness(transcripts=[_transcript(_UUID_A, ws)], records=[_record(_UUID_B, ws)])
    transcripts, live = _gather([harness], _env(tmp_path), monkeypatch, run=_ok())
    assert [t.session_id for t in transcripts] == [_UUID_A]
    assert [r.session_id for r in live] == [_UUID_B]


def test_no_harness_fails_closed(tmp_path: Path, monkeypatch) -> None:
    from camp.launch.teardown_guard import EnumerationUnavailable

    with pytest.raises(EnumerationUnavailable):
        _gather([], _env(tmp_path), monkeypatch)


def test_an_unreadable_transcript_store_fails_closed(tmp_path: Path, monkeypatch) -> None:
    from camp.launch.teardown_guard import EnumerationUnavailable

    harness = _Harness(transcripts=None)
    with pytest.raises(EnumerationUnavailable):
        _gather([harness], _env(tmp_path), monkeypatch, run=_ok())


def test_a_harness_with_no_enumeration_concept_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    from camp.launch.teardown_guard import EnumerationUnavailable

    harness = _Harness(enumerate_argv=())
    with pytest.raises(EnumerationUnavailable):
        _gather([harness], _env(tmp_path), monkeypatch, run=_ok())


def test_a_probe_that_times_out_fails_closed(tmp_path: Path, monkeypatch) -> None:
    from camp.launch.teardown_guard import EnumerationUnavailable

    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1.0)

    harness = _Harness()
    with pytest.raises(EnumerationUnavailable):
        _gather([harness], _env(tmp_path), monkeypatch, run=run)


def test_a_probe_camp_cannot_parse_fails_closed(tmp_path: Path, monkeypatch) -> None:
    from camp.launch.teardown_guard import EnumerationUnavailable

    class _Unparsable(_Harness):
        def parse_session_list(self, output):
            raise ValueError("garbage")

    with pytest.raises(EnumerationUnavailable):
        _gather([_Unparsable()], _env(tmp_path), monkeypatch, run=_ok())


def test_an_uninstalled_harness_binary_is_an_answer(tmp_path: Path, monkeypatch) -> None:
    """Nothing can be running under a binary that is not installed — that is an
    answer of zero live sessions, not an unanswerable seam."""

    def run(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    transcripts, live = _gather([_Harness()], _env(tmp_path), monkeypatch, run=run)
    assert live == []


def test_a_probe_that_answered_nonzero_is_an_answer(tmp_path: Path, monkeypatch) -> None:
    """A probe that ran and reported "no sessions" the only way it can — a
    nonzero exit, the way `tmux list-sessions` reports a dead server — has
    answered. Camp read the exit status; it is not guessing."""

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "no server running")

    transcripts, live = _gather([_Harness()], _env(tmp_path), monkeypatch, run=run)
    assert live == []


# ---------------------------------------------------------------------------
# The refusal
# ---------------------------------------------------------------------------


def test_the_refusal_names_every_blocking_session(tmp_path: Path) -> None:
    from camp.launch.teardown_guard import render_block

    env = _env(tmp_path)
    ws = _workspace(tmp_path, "g", "ws")
    member = ws / "repo_a"
    member.mkdir()
    blocking = _blocking(
        ws,
        transcripts=[_transcript(_UUID_B, member)],
        live_records=[_record(_UUID_A, ws)],
        groups=[_group("g")],
        env=env,
    )
    rendered = render_block("ws", blocking)
    assert "ws" in rendered
    for candidate in blocking:
        assert candidate.derived_name in rendered
        assert str(candidate.root) in rendered
    assert "--force" in rendered
    assert "camp kill" in rendered


def test_the_refusal_escapes_a_control_character_in_a_root(tmp_path: Path) -> None:
    """A root reaches camp from a transcript camp did not write; an embedded
    newline would turn one refusal line into two."""
    from camp.launch.recovery import SessionCandidate
    from camp.launch.teardown_guard import render_block

    candidate = SessionCandidate(
        session_id=_UUID_A,
        derived_name="camp-ws-aaaaaaaa",
        root=Path("/tmp/evil\nx"),
        age_seconds=1.0,
        live=False,
        root_missing=False,
        unreadable=False,
    )
    rendered = render_block("ws", (candidate,))
    assert "evil\nx" not in rendered
    assert "\\x0a" in rendered


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------


def test_the_guard_module_neither_prints_nor_exits() -> None:
    """The module decides and renders; the CLI prints and exits."""
    source = (_PLUGIN_DIR / "camp" / "launch" / "teardown_guard.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "print" not in called
    attrs = {
        f"{node.value.id}.{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    }
    assert "sys.exit" not in attrs
    assert "os.environ" not in attrs
