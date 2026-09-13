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

`camp.transfer.release` (the sender's own post-claim archive step) is also
covered here, hermetically:

- A conversation's transcript is relocated into the archive with its bytes
  identical to what was on disk before the move.
- Re-running the release for an already-archived conversation whose source
  transcript no longer exists on this host reports `ALREADY_ARCHIVED`, never
  a second `ARCHIVED`, and the durable marker gains no second entry for it.
  A live source found at the destination's session id — a round-tripped
  conversation sent outward again — is always relocated instead, replacing
  the stale archive.
- A marker-append failure that happens after the transcript has already been
  relocated reports the destination it moved to, not `None`.
- Two distinct conversations that share a subpath archive to two distinct,
  non-colliding destinations, keyed by session id rather than by path.
- A relocation across a filesystem boundary (`os.rename` raising as it would
  for `EXDEV`) still succeeds, via `shutil.move`'s copy-then-remove fallback.
- An unwritable archive destination is reported `FAILED`, distinguishable
  from `ARCHIVED` and carrying a detail naming why.
- In a multi-conversation release where one conversation's relocation fails,
  every conversation is still reported individually — the failure of one
  never suppresses or aggregates the outcome of the others.
- An empty conversation pool releases cleanly and creates no archive
  directory.

Every path comes from ``tmp_path`` and every group state dir from an injected
``CAMP_STATE_DIR``, so no test reads the operator's real state.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

# `camp.transfer.release` reaches `trailhead.paths` (via `central_state_dir`),
# which is not importable from a bare `PYTHONPATH=plugins/camp` invocation —
# bootstrap it here exactly as `test_transfer_cli.py` does.
import _bootstrap  # noqa: E402

_bootstrap.ensure_trailhead_importable()

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


# ---------------------------------------------------------------------------
# camp.transfer.release — the sender's own post-claim archive step.
# ---------------------------------------------------------------------------


def _crossed(session_id: str, subpath: PurePosixPath = PurePosixPath(".")):
    from camp.transfer.move import ConversationCrossed

    return ConversationCrossed(session_id=session_id, subpath=subpath)


def _seed_transcript(root: Path, session_id: str, content: bytes) -> Path:
    path = root / f"{session_id}.jsonl"
    path.write_bytes(content)
    return path


def test_release_relocates_the_transcript_byte_identical(tmp_path: Path) -> None:
    from camp.transfer.release import ReleaseOutcome, release_conversations

    env = _env(tmp_path)
    source_root = tmp_path / "harness-store"
    source_root.mkdir()
    content = "line one\nline two — π marker\n".encode("utf-8")
    transcript = _seed_transcript(source_root, _UUID_A, content)
    before_bytes = transcript.read_bytes()

    results = release_conversations(
        group="g",
        slug="ws",
        workspace_root=tmp_path / "ws",
        conversations=(_crossed(_UUID_A),),
        locate_transcript=lambda sid, root: transcript,
        env=env,
    )

    assert len(results) == 1
    assert results[0].outcome is ReleaseOutcome.ARCHIVED
    assert results[0].archive_path is not None
    assert results[0].archive_path.read_bytes() == before_bytes


def test_rerun_after_archiving_reports_already_archived_and_marker_gains_no_second_entry(
    tmp_path: Path,
) -> None:
    from camp.transfer.release import ReleaseOutcome, read_release_marker, release_conversations

    env = _env(tmp_path)
    source_root = tmp_path / "harness-store"
    source_root.mkdir()
    transcript = _seed_transcript(source_root, _UUID_A, b"first run content\n")

    first = release_conversations(
        group="g",
        slug="ws",
        workspace_root=tmp_path / "ws",
        conversations=(_crossed(_UUID_A),),
        locate_transcript=lambda sid, root: transcript,
        env=env,
    )
    assert first[0].outcome is ReleaseOutcome.ARCHIVED

    # The first run already relocated the transcript away from its source —
    # a real re-run's `locate_transcript` (the harness store lookup) finds
    # nothing there any more, exactly like this closure. It IS consulted
    # (see `test_marker_append_failure_still_reports_where_the_transcript_
    # already_moved_to` and the module docstring for why a live source must
    # always be checked), it just has nothing to report.
    second = release_conversations(
        group="g",
        slug="ws",
        workspace_root=tmp_path / "ws",
        conversations=(_crossed(_UUID_A),),
        locate_transcript=lambda sid, root: None,
        env=env,
    )
    assert second[0].outcome is ReleaseOutcome.ALREADY_ARCHIVED

    marker = read_release_marker("g", "ws", env=env)
    matching = [entry for entry in marker if entry["session_id"] == _UUID_A]
    assert len(matching) == 1, marker


def test_two_conversations_sharing_a_subpath_archive_to_distinct_destinations(
    tmp_path: Path,
) -> None:
    from camp.transfer.release import ReleaseOutcome, release_conversations

    env = _env(tmp_path)
    source_root = tmp_path / "harness-store"
    source_root.mkdir()
    transcript_a = _seed_transcript(source_root, _UUID_A, b"conversation A content\n")
    transcript_b = _seed_transcript(source_root, _UUID_B, b"conversation B content\n")

    def _locate(session_id: str, root: Path) -> Path:
        return transcript_a if session_id == _UUID_A else transcript_b

    results = release_conversations(
        group="g",
        slug="ws",
        workspace_root=tmp_path / "ws",
        conversations=(_crossed(_UUID_A), _crossed(_UUID_B)),
        locate_transcript=_locate,
        env=env,
    )

    by_id = {r.session_id: r for r in results}
    assert by_id[_UUID_A].outcome is ReleaseOutcome.ARCHIVED
    assert by_id[_UUID_B].outcome is ReleaseOutcome.ARCHIVED
    assert by_id[_UUID_A].archive_path != by_id[_UUID_B].archive_path
    assert by_id[_UUID_A].archive_path.read_bytes() == b"conversation A content\n"
    assert by_id[_UUID_B].archive_path.read_bytes() == b"conversation B content\n"


def test_relocation_across_a_filesystem_boundary_still_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from camp.transfer.release import ReleaseOutcome, release_conversations

    env = _env(tmp_path)
    source_root = tmp_path / "harness-store"
    source_root.mkdir()
    transcript = _seed_transcript(source_root, _UUID_A, b"cross-device content\n")

    real_rename = os.rename
    calls = {"raised": 0}

    def _cross_device_rename(src, dst):
        calls["raised"] += 1
        raise OSError(18, "Invalid cross-device link")  # errno.EXDEV on Linux

    monkeypatch.setattr(os, "rename", _cross_device_rename)
    try:
        results = release_conversations(
            group="g",
            slug="ws",
            workspace_root=tmp_path / "ws",
            conversations=(_crossed(_UUID_A),),
            locate_transcript=lambda sid, root: transcript,
            env=env,
        )
    finally:
        monkeypatch.setattr(os, "rename", real_rename)

    assert calls["raised"] > 0, "the fallback was never exercised"
    assert results[0].outcome is ReleaseOutcome.ARCHIVED
    assert results[0].archive_path.read_bytes() == b"cross-device content\n"
    assert not transcript.exists()


def test_unwritable_archive_destination_is_reported_failed_distinct_from_success(
    tmp_path: Path,
) -> None:
    from camp.transfer.release import ReleaseOutcome, archive_dir, release_conversations

    env = _env(tmp_path)
    source_root = tmp_path / "harness-store"
    source_root.mkdir()
    transcript = _seed_transcript(source_root, _UUID_A, b"blocked content\n")

    root = archive_dir("g", "ws", env=env)
    root.mkdir(parents=True)
    root.chmod(0o500)
    try:
        results = release_conversations(
            group="g",
            slug="ws",
            workspace_root=tmp_path / "ws",
            conversations=(_crossed(_UUID_A),),
            locate_transcript=lambda sid, root: transcript,
            env=env,
        )
    finally:
        root.chmod(0o700)

    assert results[0].outcome is ReleaseOutcome.FAILED
    assert results[0].outcome is not ReleaseOutcome.ARCHIVED
    assert results[0].archive_path is None
    assert results[0].detail


def test_a_failed_replacement_leaves_the_prior_archive_intact_rather_than_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stale archive used to be deleted before the replacement move, so
    a replacement that then failed left neither a complete archive nor a
    marker entry for it — worse than what a retry started with. A failed
    replacement must instead leave the prior archive exactly as it was."""
    import shutil as shutil_module

    from camp.transfer.release import ReleaseOutcome, archive_dir, release_conversations

    env = _env(tmp_path)
    source_root = tmp_path / "harness-store"
    source_root.mkdir()
    transcript = _seed_transcript(source_root, _UUID_A, b"fresh-content\n")

    root = archive_dir("g", "ws", env=env)
    root.mkdir(parents=True)
    stale_dest = root / f"{_UUID_A}.jsonl"
    stale_dest.write_bytes(b"stale-content\n")

    real_move = shutil_module.move

    def _move_that_fails_moving_the_transcript_in(src, dst, *a, **kw):
        if str(src) == str(transcript):
            raise OSError("simulated replacement failure")
        return real_move(src, dst, *a, **kw)

    monkeypatch.setattr(shutil_module, "move", _move_that_fails_moving_the_transcript_in)

    results = release_conversations(
        group="g",
        slug="ws",
        workspace_root=tmp_path / "ws",
        conversations=(_crossed(_UUID_A),),
        locate_transcript=lambda sid, root: transcript,
        env=env,
    )

    assert results[0].outcome is ReleaseOutcome.FAILED
    assert stale_dest.is_file()
    assert stale_dest.read_bytes() == b"stale-content\n"


def test_a_failure_relocating_the_nested_subtree_after_the_transcript_moved_reports_the_archived_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The nested subtree used to relocate BEFORE the top-level transcript,
    so a failure between the two reported `archive_path=None` — "nothing
    moved" — while the nested content had, in fact, already left the
    harness's store. With the transcript relocated first, a failure
    relocating only the nested subtree must report the path the transcript
    actually landed at, never `None`."""
    import shutil as shutil_module

    from camp.transfer.release import ReleaseOutcome, release_conversations

    env = _env(tmp_path)
    source_root = tmp_path / "harness-store"
    source_root.mkdir()
    transcript = _seed_transcript(source_root, _UUID_A, b"top-level content\n")
    nested_source = source_root / _UUID_A
    nested_source.mkdir()
    (nested_source / "subagent.jsonl").write_bytes(b"nested content\n")

    real_move = shutil_module.move

    def _move_that_fails_on_the_nested_dir(src, dst, *a, **kw):
        if str(src) == str(nested_source):
            raise OSError("simulated nested relocation failure")
        return real_move(src, dst, *a, **kw)

    monkeypatch.setattr(shutil_module, "move", _move_that_fails_on_the_nested_dir)

    results = release_conversations(
        group="g",
        slug="ws",
        workspace_root=tmp_path / "ws",
        conversations=(_crossed(_UUID_A),),
        locate_transcript=lambda sid, root: transcript,
        env=env,
    )

    assert len(results) == 1
    assert results[0].outcome is ReleaseOutcome.FAILED
    assert results[0].archive_path is not None
    assert results[0].archive_path.is_file()
    assert results[0].archive_path.read_bytes() == b"top-level content\n"


def test_multi_conversation_release_reports_each_outcome_individually_on_partial_failure(
    tmp_path: Path,
) -> None:
    from camp.transfer.release import ReleaseOutcome, release_conversations

    env = _env(tmp_path)
    source_root = tmp_path / "harness-store"
    source_root.mkdir()
    transcript_a = _seed_transcript(source_root, _UUID_A, b"conversation A\n")
    missing_b_path = source_root / f"{_UUID_B}.jsonl"  # never written — vanished
    session_c = "cccccccc-3333-4333-8333-333333333333"
    transcript_c = _seed_transcript(source_root, session_c, b"conversation C\n")

    def _locate(session_id: str, root: Path):
        return {
            _UUID_A: transcript_a,
            _UUID_B: missing_b_path,
            session_c: transcript_c,
        }[session_id]

    results = release_conversations(
        group="g",
        slug="ws",
        workspace_root=tmp_path / "ws",
        conversations=(_crossed(_UUID_A), _crossed(_UUID_B), _crossed(session_c)),
        locate_transcript=_locate,
        env=env,
    )

    by_id = {r.session_id: r for r in results}
    assert len(by_id) == 3
    assert by_id[_UUID_A].outcome is ReleaseOutcome.ARCHIVED
    assert by_id[session_c].outcome is ReleaseOutcome.ARCHIVED
    assert by_id[_UUID_B].outcome is ReleaseOutcome.FAILED
    assert by_id[_UUID_B].detail


def test_empty_conversation_pool_releases_cleanly_with_no_archive_directory(
    tmp_path: Path,
) -> None:
    from camp.transfer.release import archive_dir, read_release_marker, release_conversations

    env = _env(tmp_path)

    results = release_conversations(
        group="g",
        slug="ws",
        workspace_root=tmp_path / "ws",
        conversations=(),
        locate_transcript=lambda sid, root: None,
        env=env,
    )

    assert results == ()
    assert not archive_dir("g", "ws", env=env).exists()
    assert read_release_marker("g", "ws", env=env) == ()


# ---------------------------------------------------------------------------
# the durable marker's mutual exclusion across more than one writer
# ---------------------------------------------------------------------------


def test_normal_sequential_release_writes_marker_entries_in_call_order(
    tmp_path: Path,
) -> None:
    from camp.transfer.release import read_release_marker, release_conversations

    env = _env(tmp_path)
    source_root = tmp_path / "harness-store"
    source_root.mkdir()
    transcript_a = _seed_transcript(source_root, _UUID_A, b"a\n")
    session_c = "cccccccc-3333-4333-8333-333333333333"
    transcript_c = _seed_transcript(source_root, session_c, b"c\n")

    def _locate(session_id: str, root: Path) -> Path:
        return {_UUID_A: transcript_a, session_c: transcript_c}[session_id]

    release_conversations(
        group="g",
        slug="ws",
        workspace_root=tmp_path / "ws",
        conversations=(_crossed(_UUID_A), _crossed(session_c)),
        locate_transcript=_locate,
        env=env,
    )

    marker = read_release_marker("g", "ws", env=env)
    assert [entry["session_id"] for entry in marker] == [_UUID_A, session_c]


def test_two_appends_racing_across_a_real_contention_window_both_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real contention window, not an ordering assertion: the first
    append's real production write is paused mid-flight, between its own
    read of the marker and its own write of it, while the second append's
    real production call is landed inside that window from a second thread.
    Neither entry may be lost."""
    from camp.transfer import release as release_module
    from camp.transfer.release import read_release_marker, release_conversations

    env = _env(tmp_path)
    source_root = tmp_path / "harness-store"
    source_root.mkdir()
    transcript_a = _seed_transcript(source_root, _UUID_A, b"first\n")
    transcript_b = _seed_transcript(source_root, _UUID_B, b"second\n")

    in_window = threading.Event()
    release_write = threading.Event()
    order: list[str] = []
    order_lock = threading.Lock()

    real_dumps = release_module.json.dumps
    paused = {"done": False}

    def _paused_dumps(data):
        if not paused["done"]:
            paused["done"] = True
            in_window.set()
            assert release_write.wait(timeout=5), "second append never attempted to land"
        return real_dumps(data)

    monkeypatch.setattr(release_module.json, "dumps", _paused_dumps)

    def _append_a() -> None:
        release_conversations(
            group="g",
            slug="ws",
            workspace_root=tmp_path / "ws",
            conversations=(_crossed(_UUID_A),),
            locate_transcript=lambda sid, root: transcript_a,
            env=env,
        )
        with order_lock:
            order.append("a-wrote")

    thread_a = threading.Thread(target=_append_a)
    thread_a.start()
    assert in_window.wait(timeout=5), "first append never reached its window"

    def _append_b() -> None:
        release_conversations(
            group="g",
            slug="ws",
            workspace_root=tmp_path / "ws",
            conversations=(_crossed(_UUID_B),),
            locate_transcript=lambda sid, root: transcript_b,
            env=env,
        )
        with order_lock:
            order.append("b-wrote")

    thread_b = threading.Thread(target=_append_b)
    thread_b.start()

    time.sleep(0.3)
    with order_lock:
        assert order == [], "second append landed before the first released the lock"

    release_write.set()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)
    assert not thread_a.is_alive()
    assert not thread_b.is_alive()

    marker = read_release_marker("g", "ws", env=env)
    ids = {entry["session_id"] for entry in marker}
    assert ids == {_UUID_A, _UUID_B}, marker


def test_append_that_cannot_take_the_exclusion_is_reported_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import camp.group.manifest as manifest_module
    from camp.transfer.release import ReleaseOutcome, read_release_marker, release_conversations

    env = _env(tmp_path)
    source_root = tmp_path / "harness-store"
    source_root.mkdir()
    transcript = _seed_transcript(source_root, _UUID_A, b"locked out\n")

    def _boom(ws_dir):
        raise OSError("simulated lock acquisition failure")

    monkeypatch.setattr(manifest_module, "reconcile_lock", _boom)

    results = release_conversations(
        group="g",
        slug="ws",
        workspace_root=tmp_path / "ws",
        conversations=(_crossed(_UUID_A),),
        locate_transcript=lambda sid, root: transcript,
        env=env,
    )

    assert results[0].outcome is ReleaseOutcome.FAILED
    assert results[0].outcome is not ReleaseOutcome.ARCHIVED
    assert results[0].detail

    marker = read_release_marker("g", "ws", env=env)
    assert marker == ()


def test_marker_append_failure_still_reports_where_the_transcript_already_moved_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The relocation itself (`shutil.move`) already succeeded by the time
    the marker append is attempted — a caller rendering this `FAILED`
    outcome needs to know the transcript is no longer at its source, not
    just that recording it failed. `archive_path` must name the destination
    the file actually landed at, not `None` (which the renderer reserves for
    "nothing moved")."""
    import camp.group.manifest as manifest_module
    from camp.transfer.release import ReleaseOutcome, archive_dir, release_conversations

    env = _env(tmp_path)
    source_root = tmp_path / "harness-store"
    source_root.mkdir()
    transcript = _seed_transcript(source_root, _UUID_A, b"moved but unmarked\n")

    def _boom(ws_dir):
        raise OSError("simulated lock acquisition failure")

    monkeypatch.setattr(manifest_module, "reconcile_lock", _boom)

    results = release_conversations(
        group="g",
        slug="ws",
        workspace_root=tmp_path / "ws",
        conversations=(_crossed(_UUID_A),),
        locate_transcript=lambda sid, root: transcript,
        env=env,
    )

    assert results[0].outcome is ReleaseOutcome.FAILED
    expected_dest = archive_dir("g", "ws", env=env) / f"{_UUID_A}.jsonl"
    assert results[0].archive_path == expected_dest
    assert expected_dest.is_file()
    assert not transcript.exists()
