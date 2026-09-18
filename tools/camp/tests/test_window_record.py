"""Tests for the per-workspace window record: file schema, atomic write, and
workspace-lock discipline.

Test contract (all must RED before implementation, GREEN after):

1. A record written and read back round-trips every field of every entry,
   for zero, one, and several entries.
2. A workspace-relative cwd is stored relative, never absolute (AC29); the
   same entry written under two different workspace roots produces the same
   stored path.
3. The record file is a different path from the manifest, and writing the
   record leaves the manifest byte-for-byte unchanged (AC30).
4. A write that fails midway leaves the previous record intact and no temp
   file behind — the reader still returns the old content (AC31).
5. A second writer blocks until the first releases, and both entries survive
   a concurrent pair of writes.
6. Calling the locked entry point while already holding the lock is the
   deadlock this split exists to prevent; the unlocked variant is what an
   already-locked caller reaches for instead.
7. An entry carrying both a conversation id and a command line, or neither,
   is rejected rather than written.

Fixtures use tmp_path directly as the workspace dir, mirroring
test_workspace_ownership.py's manifest tests — no group/env scaffolding
needed since window_record operates on a bare directory + reconcile_lock.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


# ---------------------------------------------------------------------------
# 1. Round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_zero_entries_round_trips(self, tmp_path):
        from camp.group.window_record import (
            read_window_record,
            window_record_path_for,
            write_window_record,
        )

        path = window_record_path_for(tmp_path)
        write_window_record(path, [])

        result = read_window_record(path)
        assert result.status == "ok"
        assert result.entries == ()

    def test_one_entry_round_trips_every_field(self, tmp_path):
        from camp.group.window_record import (
            WindowEntry,
            read_window_record,
            window_record_path_for,
            write_window_record,
        )

        path = window_record_path_for(tmp_path)
        entry = WindowEntry(
            window_id="@3",
            name="scratch",
            cwd="repo_a/sub",
            conversation_id="conv-123",
        )
        write_window_record(path, [entry])

        result = read_window_record(path)
        assert result.status == "ok"
        assert result.entries == (entry,)

    def test_several_entries_round_trip_depends_on_what_was_written(self, tmp_path):
        from camp.group.window_record import (
            WindowEntry,
            read_window_record,
            window_record_path_for,
            write_window_record,
        )

        path = window_record_path_for(tmp_path)
        entries = [
            WindowEntry(window_id="@1", name="a", cwd="repo_a", conversation_id="c1"),
            WindowEntry(window_id="@2", name="b", cwd="repo_b", command_line="echo hi"),
            WindowEntry(window_id="@3", name="c", cwd=".", conversation_id="c3"),
        ]
        write_window_record(path, entries)

        result = read_window_record(path)
        assert result.status == "ok"
        assert result.entries == tuple(entries)

        # A different set of entries produces a different read — the answer
        # depends on what was written, not a fixed shape.
        other = [WindowEntry(window_id="@9", name="z", cwd="x", command_line="ls")]
        write_window_record(path, other)
        result2 = read_window_record(path)
        assert result2.entries == tuple(other)
        assert result2.entries != result.entries


# ---------------------------------------------------------------------------
# 2. Relative cwd (AC29)
# ---------------------------------------------------------------------------


class TestRelativeCwd:
    def test_absolute_cwd_is_rejected_at_entry_construction(self):
        from camp.group.window_record import WindowEntry, WindowRecordError

        with pytest.raises(WindowRecordError):
            WindowEntry(
                window_id="@1",
                name="a",
                cwd="/abs/path",
                conversation_id="c1",
            )

    def test_no_absolute_path_appears_in_the_written_file(self, tmp_path):
        from camp.group.window_record import (
            WindowEntry,
            window_record_path_for,
            write_window_record,
        )

        path = window_record_path_for(tmp_path)
        entry = WindowEntry(
            window_id="@1", name="a", cwd="repo_a/nested", conversation_id="c1"
        )
        write_window_record(path, [entry])

        raw = path.read_text(encoding="utf-8")
        assert str(tmp_path) not in raw
        assert "/abs" not in raw

    def test_same_entry_under_two_roots_produces_same_stored_path(self, tmp_path):
        from camp.group.window_record import (
            WindowEntry,
            read_window_record,
            window_record_path_for,
            write_window_record,
        )

        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        root_a.mkdir()
        root_b.mkdir()

        entry = WindowEntry(
            window_id="@1", name="a", cwd="repo_a/nested", conversation_id="c1"
        )

        path_a = window_record_path_for(root_a)
        path_b = window_record_path_for(root_b)
        write_window_record(path_a, [entry])
        write_window_record(path_b, [entry])

        result_a = read_window_record(path_a)
        result_b = read_window_record(path_b)
        assert result_a.entries[0].cwd == result_b.entries[0].cwd == "repo_a/nested"


# ---------------------------------------------------------------------------
# 3. Distinct path from manifest, manifest untouched (AC30)
# ---------------------------------------------------------------------------


class TestDistinctFromManifest:
    def test_record_path_differs_from_manifest_path(self, tmp_path):
        from camp.group.window_record import window_record_path_for

        ws_dir = tmp_path
        record_path = window_record_path_for(ws_dir)
        # manifest_path_for derives from group/slug resolution; compare
        # directly against the sibling convention manifest.py documents:
        # manifest.json lives directly inside ws_dir.
        manifest_path = ws_dir / "manifest.json"
        assert record_path != manifest_path
        assert record_path.name != manifest_path.name

    def test_writing_record_leaves_manifest_byte_for_byte_unchanged(self, tmp_path):
        from camp.group.manifest import write_central_manifest
        from camp.group.window_record import (
            WindowEntry,
            window_record_path_for,
            write_window_record,
        )

        manifest_path = tmp_path / "manifest.json"
        write_central_manifest(
            manifest_path,
            {"schema_version": 1, "group": "g", "slug": "s", "branch": "worktree-s", "members": []},
        )
        before = manifest_path.read_bytes()

        record_path = window_record_path_for(tmp_path)
        entry = WindowEntry(window_id="@1", name="a", cwd="repo_a", conversation_id="c1")
        write_window_record(record_path, [entry])

        after = manifest_path.read_bytes()
        assert after == before


# ---------------------------------------------------------------------------
# 4. Write failure midway (AC31)
# ---------------------------------------------------------------------------


class TestWriteFailureMidway:
    def test_failed_write_leaves_previous_record_intact_and_no_temp_file(
        self, tmp_path, monkeypatch
    ):
        from camp.group import window_record as wr_module
        from camp.group.window_record import (
            WindowEntry,
            read_window_record,
            window_record_path_for,
            write_window_record,
        )

        path = window_record_path_for(tmp_path)
        original = [
            WindowEntry(window_id="@1", name="a", cwd="repo_a", conversation_id="c1")
        ]
        write_window_record(path, original)
        original_bytes = path.read_bytes()

        def boom(*args, **kwargs):
            raise OSError("simulated midway failure")

        monkeypatch.setattr(wr_module.os, "replace", boom)

        new_entries = [
            WindowEntry(window_id="@2", name="b", cwd="repo_b", command_line="ls")
        ]
        with pytest.raises(OSError):
            write_window_record(path, new_entries)

        # Old content intact.
        assert path.read_bytes() == original_bytes
        result = read_window_record(path)
        assert result.status == "ok"
        assert result.entries == tuple(original)

        # No temp file left behind.
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".windows-")]
        assert leftovers == []


# ---------------------------------------------------------------------------
# 5. Concurrent writers (workspace lock)
# ---------------------------------------------------------------------------


class TestConcurrentWriters:
    def test_second_writer_blocks_until_first_releases(self, tmp_path):
        from camp.group.manifest import reconcile_lock
        from camp.group.window_record import (
            WindowEntry,
            append_window_entry,
            read_window_record,
            window_record_path_for,
        )

        write_window_record_path = window_record_path_for(tmp_path)
        write_window_record_path.parent.mkdir(parents=True, exist_ok=True)

        second_done = threading.Event()

        def second_writer():
            append_window_entry(
                tmp_path,
                WindowEntry(window_id="@2", name="b", cwd="repo_b", command_line="ls"),
            )
            second_done.set()

        with reconcile_lock(tmp_path):
            t = threading.Thread(target=second_writer)
            t.start()
            # Give the second writer every chance to (wrongly) proceed while
            # the lock is held.
            still_blocked = not second_done.wait(timeout=0.5)
            assert still_blocked, "second writer proceeded while lock was held"

        # Released — the second writer should now complete promptly.
        t.join(timeout=10)
        assert second_done.is_set()

        result = read_window_record(write_window_record_path)
        assert result.status == "ok"
        assert [e.window_id for e in result.entries] == ["@2"]

    def test_both_entries_survive_a_concurrent_pair_of_writes(self, tmp_path):
        from camp.group.window_record import (
            WindowEntry,
            append_window_entry,
            read_window_record,
            window_record_path_for,
        )

        path = window_record_path_for(tmp_path)

        def writer(window_id, cwd):
            append_window_entry(
                tmp_path,
                WindowEntry(
                    window_id=window_id, name=window_id, cwd=cwd, conversation_id="c"
                ),
            )

        t1 = threading.Thread(target=writer, args=("@1", "repo_a"))
        t2 = threading.Thread(target=writer, args=("@2", "repo_b"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        result = read_window_record(path)
        assert result.status == "ok"
        assert {e.window_id for e in result.entries} == {"@1", "@2"}


# ---------------------------------------------------------------------------
# 6. Locked / _unlocked split
# ---------------------------------------------------------------------------


class TestLockedUnlockedSplit:
    def test_unlocked_variant_is_what_an_already_locked_caller_reaches_for(
        self, tmp_path
    ):
        from camp.group.manifest import reconcile_lock
        from camp.group.window_record import (
            WindowEntry,
            append_window_entry_unlocked,
            read_window_record,
            window_record_path_for,
        )

        path = window_record_path_for(tmp_path)
        entry = WindowEntry(window_id="@1", name="a", cwd="repo_a", conversation_id="c1")

        # An already-locked caller uses the unlocked entry point directly,
        # in the same thread, without re-acquiring — and it completes.
        with reconcile_lock(tmp_path):
            append_window_entry_unlocked(path, entry)

        result = read_window_record(path)
        assert result.status == "ok"
        assert result.entries == (entry,)

    def test_locked_entry_point_blocks_a_second_caller_while_lock_is_held(
        self, tmp_path
    ):
        # This is the deadlock the unlocked split exists to prevent: the
        # locked entry point (append_window_entry) always re-acquires the
        # workspace lock, so a caller that reaches for it while ALREADY
        # holding that lock (from a different holder here, since re-testing
        # true same-thread reentrancy would hang the suite forever) blocks.
        from camp.group.manifest import reconcile_lock
        from camp.group.window_record import (
            WindowEntry,
            append_window_entry,
        )

        blocked = threading.Event()
        finished = threading.Event()

        def contender():
            blocked.set()
            append_window_entry(
                tmp_path,
                WindowEntry(window_id="@2", name="b", cwd="repo_b", command_line="ls"),
            )
            finished.set()

        with reconcile_lock(tmp_path):
            t = threading.Thread(target=contender)
            t.start()
            blocked.wait(timeout=5)
            proceeded = finished.wait(timeout=0.5)
            assert not proceeded, (
                "locked entry point proceeded while the workspace lock was "
                "already held — the split gives no protection"
            )

        t.join(timeout=10)
        assert finished.is_set()


# ---------------------------------------------------------------------------
# 7. Exactly-one-of conversation_id / command_line
# ---------------------------------------------------------------------------


class TestExactlyOneOf:
    def test_both_conversation_id_and_command_line_is_rejected(self):
        from camp.group.window_record import WindowEntry, WindowRecordError

        with pytest.raises(WindowRecordError):
            WindowEntry(
                window_id="@1",
                name="a",
                cwd="repo_a",
                conversation_id="c1",
                command_line="ls",
            )

    def test_neither_conversation_id_nor_command_line_is_rejected(self):
        from camp.group.window_record import WindowEntry, WindowRecordError

        with pytest.raises(WindowRecordError):
            WindowEntry(window_id="@1", name="a", cwd="repo_a")


# ---------------------------------------------------------------------------
# 8. Missing vs. corrupt is a distinction at the reader's own boundary
# ---------------------------------------------------------------------------


class TestMissingVsCorrupt:
    """`read_window_record`'s `.status` is the boundary a later caller (AC32's
    degrading commands, and AC33's resurrection refusal) tells "no windows
    recorded" apart from "camp cannot tell" at. Collapsing the two here is
    the exact fail-open shape this task exists to avoid reproducing — pin
    both directions so a future edit that merges them goes red here first.
    """

    def test_no_file_at_all_reads_missing_with_no_error(self, tmp_path):
        from camp.group.window_record import read_window_record, window_record_path_for

        result = read_window_record(window_record_path_for(tmp_path))

        assert result.status == "missing"
        assert result.entries == ()
        assert result.error is None

    def test_present_but_unparseable_file_reads_corrupt_with_an_error(self, tmp_path):
        from camp.group.window_record import window_record_path_for

        path = window_record_path_for(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json at all")

        from camp.group.window_record import read_window_record

        result = read_window_record(path)

        assert result.status == "corrupt"
        assert result.entries == ()
        assert result.error is not None

    def test_missing_and_corrupt_are_distinguishable_results(self, tmp_path):
        """The two states differ at every field a caller can branch on —
        not merely in name — so a caller cannot accidentally treat one as
        the other by comparing only `.entries` or only `.error`.
        """
        from camp.group.window_record import read_window_record, window_record_path_for

        missing_path = window_record_path_for(tmp_path / "missing-ws")
        corrupt_path = window_record_path_for(tmp_path / "corrupt-ws")
        corrupt_path.parent.mkdir(parents=True, exist_ok=True)
        corrupt_path.write_text("{not valid json at all")

        missing = read_window_record(missing_path)
        corrupt = read_window_record(corrupt_path)

        assert missing.status != corrupt.status
        assert missing.error != corrupt.error

    def test_a_truncated_record_reads_corrupt_not_as_an_empty_ok_record(self, tmp_path):
        """A prefix of a valid record (a write killed mid-flight) must not be
        mistaken for a legitimately empty ``{"schema_version": 1, "windows":
        []}`` record — both would have zero entries, so the status field is
        the only thing that can tell them apart.
        """
        from camp.group.window_record import (
            WindowEntry,
            read_window_record,
            window_record_path_for,
            write_window_record,
        )

        path = window_record_path_for(tmp_path)
        write_window_record(
            path,
            [WindowEntry(window_id="@1", name="main", cwd=".", conversation_id="c1")],
        )
        full_bytes = path.read_bytes()
        path.write_bytes(full_bytes[: len(full_bytes) // 2])

        truncated = read_window_record(path)
        empty = read_window_record(window_record_path_for(tmp_path / "genuinely-empty"))

        assert truncated.status == "corrupt"
        assert truncated.status != empty.status
        assert truncated.entries == ()
