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

import os
import signal
import subprocess
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

    def test_escaping_relative_cwd_is_rejected_at_entry_construction(self):
        """An absolute path is one way to point `Tmux.new_window`'s -c
        outside the workspace root a later slice joins `ws_dir / entry.cwd`
        against — a `..`-laden relative path escapes exactly as effectively
        and must be rejected the same way."""
        from camp.group.window_record import WindowEntry, WindowRecordError

        with pytest.raises(WindowRecordError):
            WindowEntry(
                window_id="@1",
                name="a",
                cwd="../../escape",
                conversation_id="c1",
            )

    def test_escaping_cwd_read_back_from_a_hand_edited_record_is_corrupt(self, tmp_path):
        """A record file is trusted input from `write_window_record`'s own
        atomic writer, but it is also a plain JSON file an operator (or a
        bug) can hand-edit. A `cwd` that escapes the workspace must not
        round-trip back into a live `WindowEntry` on read."""
        import json

        from camp.group.window_record import read_window_record, window_record_path_for

        path = window_record_path_for(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "windows": [
                        {
                            "window_id": "@1",
                            "name": "a",
                            "cwd": "../../escape",
                            "conversation_id": "c1",
                            "command_line": None,
                        }
                    ],
                }
            )
        )

        result = read_window_record(path)

        assert result.status == "corrupt"
        assert result.entries == ()

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


# ---------------------------------------------------------------------------
# 9. schema_version is read, not merely written
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    """`write_window_record` always stamps `schema_version: 1`, but nothing
    read it back — a record from an unrecognised future schema, or with no
    version key at all, round-tripped as an ordinary "ok" record. Three
    later slices read and extend this format, so an unrecognised version
    must be treated as camp cannot safely interpret this content, the same
    "corrupt" state a malformed shape already gets — never silently read as
    if it were schema v1.
    """

    def test_an_unrecognised_future_schema_version_reads_corrupt(self, tmp_path):
        import json

        from camp.group.window_record import read_window_record, window_record_path_for

        path = window_record_path_for(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": 99, "windows": []}))

        result = read_window_record(path)

        assert result.status == "corrupt"
        assert result.entries == ()
        assert result.error is not None

    def test_a_record_with_no_schema_version_key_at_all_reads_corrupt(self, tmp_path):
        import json

        from camp.group.window_record import read_window_record, window_record_path_for

        path = window_record_path_for(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"windows": []}))

        result = read_window_record(path)

        assert result.status == "corrupt"
        assert result.entries == ()

    def test_the_current_schema_version_still_reads_ok(self, tmp_path):
        """The positive half — varying the version to the value
        `write_window_record` actually stamps must still read as `ok`, so
        the new check discriminates rather than rejecting everything."""
        from camp.group.window_record import (
            WindowEntry,
            read_window_record,
            window_record_path_for,
            write_window_record,
        )

        path = window_record_path_for(tmp_path)
        write_window_record(
            path, [WindowEntry(window_id="@1", name="a", cwd=".", conversation_id="c1")]
        )

        result = read_window_record(path)

        assert result.status == "ok"
        assert len(result.entries) == 1


# ---------------------------------------------------------------------------
# 10. A non-file path (directory, dangling symlink) must not fail open
# ---------------------------------------------------------------------------


class TestNonFilePath:
    """`read_window_record` treated "not `path.is_file()`" as synonymous
    with "missing" — the permissive, expected-state answer. A directory or
    a dangling symlink sitting at the record path is a different, genuinely
    unknown state ("could not tell") and must not inherit the permissive
    branch."""

    def test_a_directory_at_the_record_path_reads_corrupt_not_missing(self, tmp_path):
        from camp.group.window_record import read_window_record, window_record_path_for

        path = window_record_path_for(tmp_path)
        path.mkdir(parents=True)

        result = read_window_record(path)

        assert result.status == "corrupt"
        assert result.entries == ()

    def test_a_dangling_symlink_at_the_record_path_reads_corrupt_not_missing(self, tmp_path):
        from camp.group.window_record import read_window_record, window_record_path_for

        path = window_record_path_for(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(tmp_path / "nowhere-at-all.json")

        result = read_window_record(path)

        assert result.status == "corrupt"
        assert result.entries == ()

    def test_a_genuinely_missing_path_still_reads_missing(self, tmp_path):
        """The positive half — an ordinary absent file, with no symlink or
        directory in its place, must still be the permissive "missing"
        answer, so the new check discriminates rather than rejecting
        everything."""
        from camp.group.window_record import read_window_record, window_record_path_for

        result = read_window_record(window_record_path_for(tmp_path))

        assert result.status == "missing"


# ---------------------------------------------------------------------------
# 11. restamp_window_entries — the post-resurrection locked read-modify-write
# ---------------------------------------------------------------------------


class TestRestampWindowEntries:
    def test_restamps_re_ided_entries_and_drops_removed_in_original_order(self, tmp_path):
        from camp.group.window_record import (
            WindowEntry,
            read_window_record,
            restamp_window_entries,
            window_record_path_for,
            write_window_record,
        )

        path = window_record_path_for(tmp_path)
        e1 = WindowEntry(window_id="@1", name="one", cwd="repo_a", conversation_id="c1")
        e2 = WindowEntry(window_id="@2", name="two", cwd="repo_b", command_line="ls")
        e3 = WindowEntry(window_id="@3", name="three", cwd="repo_c", command_line="pwd")
        write_window_record(path, [e1, e2, e3])

        new_e1 = WindowEntry(window_id="@10", name="one", cwd="repo_a", conversation_id="c1")
        new_e2 = WindowEntry(window_id="@20", name="two", cwd="repo_b", command_line="ls")
        mapping = {"@1": new_e1, "@2": new_e2}

        outcome = restamp_window_entries(
            tmp_path, mapping, remove={"@3"}, lock_timeout=None
        )

        assert outcome.__class__.__name__ == "Restamped"
        result = read_window_record(path)
        assert result.status == "ok"
        assert [e.window_id for e in result.entries] == ["@10", "@20"]
        assert result.entries[0] == new_e1
        assert result.entries[1] == new_e2

    def test_empty_remove_keeps_all_entries(self, tmp_path):
        from camp.group.window_record import (
            WindowEntry,
            read_window_record,
            restamp_window_entries,
            window_record_path_for,
            write_window_record,
        )

        path = window_record_path_for(tmp_path)
        e1 = WindowEntry(window_id="@1", name="one", cwd="repo_a", conversation_id="c1")
        e2 = WindowEntry(window_id="@2", name="two", cwd="repo_b", command_line="ls")
        e3 = WindowEntry(window_id="@3", name="three", cwd="repo_c", command_line="pwd")
        write_window_record(path, [e1, e2, e3])

        new_e1 = WindowEntry(window_id="@10", name="one", cwd="repo_a", conversation_id="c1")
        new_e2 = WindowEntry(window_id="@20", name="two", cwd="repo_b", command_line="ls")
        mapping = {"@1": new_e1, "@2": new_e2}

        outcome = restamp_window_entries(tmp_path, mapping, remove=set(), lock_timeout=None)

        assert outcome.__class__.__name__ == "Restamped"
        result = read_window_record(path)
        assert [e.window_id for e in result.entries] == ["@10", "@20", "@3"]

    def test_entry_appended_between_plan_and_restamp_survives_in_position(self, tmp_path):
        from camp.group.window_record import (
            WindowEntry,
            read_window_record,
            restamp_window_entries,
            window_record_path_for,
            write_window_record,
        )

        path = window_record_path_for(tmp_path)
        e1 = WindowEntry(window_id="@1", name="one", cwd="repo_a", conversation_id="c1")
        e2 = WindowEntry(window_id="@2", name="two", cwd="repo_b", command_line="ls")
        write_window_record(path, [e1, e2])

        # Simulate a concurrent appender writing a fourth entry after the
        # plan was computed but before the restamp runs.
        appended = WindowEntry(window_id="@4", name="four", cwd="repo_d", command_line="date")
        write_window_record(path, [e1, e2, appended])

        new_e1 = WindowEntry(window_id="@10", name="one", cwd="repo_a", conversation_id="c1")
        mapping = {"@1": new_e1}

        outcome = restamp_window_entries(tmp_path, mapping, remove=set(), lock_timeout=None)

        assert outcome.__class__.__name__ == "Restamped"
        result = read_window_record(path)
        assert [e.window_id for e in result.entries] == ["@10", "@2", "@4"]
        assert result.entries[2] == appended

    def test_mapping_name_wins_over_recorded_name(self, tmp_path):
        from camp.group.window_record import (
            WindowEntry,
            read_window_record,
            restamp_window_entries,
            window_record_path_for,
            write_window_record,
        )

        path = window_record_path_for(tmp_path)
        e1 = WindowEntry(window_id="@1", name="stale-name", cwd="repo_a", conversation_id="c1")
        write_window_record(path, [e1])

        new_e1 = WindowEntry(
            window_id="@10", name="tmux-read-back-name", cwd="repo_a", conversation_id="c1"
        )
        mapping = {"@1": new_e1}

        outcome = restamp_window_entries(tmp_path, mapping, remove=set(), lock_timeout=None)

        assert outcome.__class__.__name__ == "Restamped"
        result = read_window_record(path)
        assert result.entries[0].name == "tmux-read-back-name"

    def test_lock_held_by_another_process_times_out_and_leaves_record_unchanged(self, tmp_path):
        """Spawns a REAL second process holding reconcile_lock — an
        in-thread hold of a non-reentrant lock does not exercise the
        timeout path this test names."""
        from camp.group.manifest import lock_path_for
        from camp.group.window_record import (
            WindowEntry,
            restamp_window_entries,
            window_record_path_for,
            write_window_record,
        )

        path = window_record_path_for(tmp_path)
        e1 = WindowEntry(window_id="@1", name="one", cwd="repo_a", conversation_id="c1")
        write_window_record(path, [e1])
        original_bytes = path.read_bytes()

        lock_path = lock_path_for(tmp_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        holder_src = (
            "import fcntl, time\n"
            f"fd = open({str(lock_path)!r}, 'w')\n"
            "fcntl.flock(fd.fileno(), fcntl.LOCK_EX)\n"
            "print('locked', flush=True)\n"
            "time.sleep(60)\n"
        )
        holder = subprocess.Popen(
            [sys.executable, "-c", holder_src],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            line = holder.stdout.readline()
            assert line.strip() == "locked", "holder subprocess never acquired the flock"

            new_e1 = WindowEntry(
                window_id="@10", name="one", cwd="repo_a", conversation_id="c1"
            )
            outcome = restamp_window_entries(
                tmp_path, {"@1": new_e1}, remove=set(), lock_timeout=0.2
            )

            assert outcome.__class__.__name__ == "NotRestamped"
            assert str(path) in outcome.reason
            assert path.read_bytes() == original_bytes
        finally:
            os.kill(holder.pid, signal.SIGKILL)
            holder.wait(timeout=5)

    def test_successful_restamp_leaves_no_temp_file_in_workspace_dir(self, tmp_path):
        from camp.group.window_record import (
            WindowEntry,
            restamp_window_entries,
            window_record_path_for,
            write_window_record,
        )

        path = window_record_path_for(tmp_path)
        e1 = WindowEntry(window_id="@1", name="one", cwd="repo_a", conversation_id="c1")
        write_window_record(path, [e1])

        new_e1 = WindowEntry(window_id="@10", name="one", cwd="repo_a", conversation_id="c1")
        outcome = restamp_window_entries(tmp_path, {"@1": new_e1}, remove=set(), lock_timeout=None)

        assert outcome.__class__.__name__ == "Restamped"
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".windows-")]
        assert leftovers == []
