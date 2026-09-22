"""Tests for launch/window_reconcile.py — the record corrected to tmux on
the window id alone.

Test contract (see
task/reconciliation-corrects-the-record-to-tmux-on-the-window-id-alone):

1. An entry whose id is absent from the live list is dropped; an entry
   whose id is present survives even if its name, cwd, and index all
   differ from what tmux shows. Record order is preserved.
2. Two live windows sharing a name with one recorded entry: only the id
   match survives — name is never a key.
3. A renamed window keeps its conversation_id and cwd and takes the new
   name; a window whose conversation has exited (foreground command is a
   shell) keeps its conversation_id untouched.
4. A live window with no entry produces no new entry and no change.
5. Identical inputs produce zero changes, and the locked wrapper then
   leaves the record file byte-for-byte unchanged (content and mtime).
6. With changes, the wrapper writes through write_window_record under the
   lock: a second locked caller blocks until the write releases (mirror
   TestConcurrentWriters).
7. A corrupt record yields NotReconciled naming the path and the file is
   untouched; UNANSWERED and None listings yield NotReconciled with no
   write.
8. render_changes returns nothing for no changes, one line per change
   otherwise; a dropped entry's line carries its conversation id (or its
   command line); every line is escaped whole, so a name carrying a
   carriage return, an ANSI escape, or any other control character is
   rendered printable.
9. No backup copy of the record is written: after a reconciling write, the
   workspace dir holds only the record file — no `.prev` or other extra
   file.

Mirrors test_window_record.py's convention: sys.path is set up for the
plugin package before any `camp.*` import, and — since importing at module
level ahead of that setup would be an import-order bug, and the ruff config
enforces E402 rather than special-casing the tests directory — every
`camp.*` symbol is imported inside the function that uses it.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _entry(window_id, name, cwd="repo", conversation_id="conv", command_line=None):
    from camp.group.window_record import WindowEntry

    if command_line is not None:
        return WindowEntry(window_id=window_id, name=name, cwd=cwd, command_line=command_line)
    return WindowEntry(window_id=window_id, name=name, cwd=cwd, conversation_id=conversation_id)


def _window(window_id, name, current_path="/ws", current_command="bash"):
    from camp.launch.tmux import TmuxWindow

    return TmuxWindow(window_id=window_id, current_path=current_path, current_command=current_command, name=name)


class _StubTmux:
    def __init__(self, answer):
        self._answer = answer

    def list_windows(self, name):
        return self._answer


class TestReconcilePure:
    def test_entry_with_no_live_match_is_dropped_present_survives_despite_drift(self):
        from camp.launch.window_reconcile import Dropped, reconcile

        surviving = _entry("@1", "planning", cwd="repo_a", conversation_id="c1")
        dropped = _entry("@2", "review", cwd="repo_b", conversation_id="c2")
        # Same name as recorded, but a different cwd and a different index
        # in the live listing (it is second, not first) — neither is a key.
        live = (
            _window("@9", name="unrelated"),
            _window("@1", name="planning", current_path="/elsewhere"),
        )

        result = reconcile([surviving, dropped], live)

        assert [e.window_id for e in result.entries] == ["@1"]
        assert result.entries[0].cwd == "repo_a"
        assert result.entries[0].conversation_id == "c1"
        assert result.changes == (Dropped(window_id="@2", name="review", conversation_id="c2"),)

    def test_record_order_is_preserved_among_survivors(self):
        from camp.launch.window_reconcile import reconcile

        e1 = _entry("@1", "a")
        e2 = _entry("@2", "b")
        e3 = _entry("@3", "c")
        live = (_window("@3", "c"), _window("@1", "a"), _window("@2", "b"))

        result = reconcile([e1, e2, e3], live)

        assert [e.window_id for e in result.entries] == ["@1", "@2", "@3"]

    def test_two_live_windows_sharing_a_name_only_id_match_survives(self):
        from camp.launch.window_reconcile import Renamed, reconcile

        # The recorded entry's id ("@1") lives on a window whose NAME has
        # since drifted to "bar". A decoy window ("@9") kept the recorded
        # name "foo" — a name-keyed match would find the decoy, see the
        # names agree, and wrongly report no rename at all.
        entry = _entry("@1", "foo", cwd="repo")
        live = (_window("@9", "foo"), _window("@1", "bar"))

        result = reconcile([entry], live)

        assert [e.window_id for e in result.entries] == ["@1"]
        assert result.entries[0].name == "bar"
        assert result.changes == (Renamed(window_id="@1", old="foo", new="bar"),)

    def test_renamed_window_keeps_conversation_id_and_cwd_and_takes_new_name(self):
        from camp.launch.window_reconcile import Renamed, reconcile

        entry = _entry("@1", "old-name", cwd="repo_a", conversation_id="conv-1")
        live = (_window("@1", "new-name", current_command="bash"),)

        result = reconcile([entry], live)

        assert len(result.entries) == 1
        assert result.entries[0].name == "new-name"
        assert result.entries[0].conversation_id == "conv-1"
        assert result.entries[0].cwd == "repo_a"
        assert result.changes == (Renamed(window_id="@1", old="old-name", new="new-name"),)

    def test_live_window_with_no_entry_produces_no_new_entry_and_no_change(self):
        from camp.launch.window_reconcile import reconcile

        entry = _entry("@1", "a")
        live = (_window("@1", "a"), _window("@2", "unrecorded"))

        result = reconcile([entry], live)

        assert [e.window_id for e in result.entries] == ["@1"]
        assert result.changes == ()

    def test_identical_inputs_produce_zero_changes(self):
        from camp.launch.window_reconcile import reconcile

        entry = _entry("@1", "a", cwd="repo")
        live = (_window("@1", "a"),)

        result = reconcile([entry], live)

        assert result.changes == ()
        assert result.entries == (entry,)


class TestReconcileWorkspaceRecord:
    def test_identical_inputs_leave_record_byte_for_byte_unchanged(self, tmp_path):
        from camp.group.window_record import window_record_path_for, write_window_record
        from camp.launch.tmux import WindowListing
        from camp.launch.window_reconcile import Reconciled, reconcile_workspace_record

        entry = _entry("@1", "a", cwd="repo")
        path = window_record_path_for(tmp_path)
        write_window_record(path, [entry])
        before_bytes = path.read_bytes()
        before_mtime_ns = path.stat().st_mtime_ns

        tmux = _StubTmux(WindowListing(windows=(_window("@1", "a"),), dropped=0))
        outcome = reconcile_workspace_record(tmp_path, "sess", tmux)

        assert isinstance(outcome, Reconciled)
        assert outcome.changes == ()
        assert path.read_bytes() == before_bytes
        assert path.stat().st_mtime_ns == before_mtime_ns

    def test_changes_are_written_through_write_window_record(self, tmp_path):
        from camp.group.window_record import (
            read_window_record,
            window_record_path_for,
            write_window_record,
        )
        from camp.launch.tmux import WindowListing
        from camp.launch.window_reconcile import Reconciled, Renamed, reconcile_workspace_record

        entry = _entry("@1", "old-name", cwd="repo", conversation_id="c1")
        path = window_record_path_for(tmp_path)
        write_window_record(path, [entry])

        tmux = _StubTmux(WindowListing(windows=(_window("@1", "new-name"),), dropped=0))
        outcome = reconcile_workspace_record(tmp_path, "sess", tmux)

        assert isinstance(outcome, Reconciled)
        assert outcome.changes == (Renamed(window_id="@1", old="old-name", new="new-name"),)
        reread = read_window_record(path)
        assert reread.status == "ok"
        assert reread.entries[0].name == "new-name"

    def test_second_locked_caller_blocks_until_first_releases(self, tmp_path):
        from camp.group.manifest import reconcile_lock
        from camp.group.window_record import (
            read_window_record,
            window_record_path_for,
            write_window_record,
        )
        from camp.launch.tmux import WindowListing
        from camp.launch.window_reconcile import reconcile_workspace_record

        entry = _entry("@1", "old-name", cwd="repo", conversation_id="c1")
        path = window_record_path_for(tmp_path)
        write_window_record(path, [entry])

        tmux = _StubTmux(WindowListing(windows=(_window("@1", "new-name"),), dropped=0))
        second_done = threading.Event()

        def second_caller():
            reconcile_workspace_record(tmp_path, "sess", tmux)
            second_done.set()

        with reconcile_lock(tmp_path):
            t = threading.Thread(target=second_caller)
            t.start()
            still_blocked = not second_done.wait(timeout=0.5)
            assert still_blocked, "second caller proceeded while lock was held"

        t.join(timeout=10)
        assert second_done.is_set()
        reread = read_window_record(path)
        assert reread.entries[0].name == "new-name"

    def test_corrupt_record_yields_not_reconciled_naming_path_and_file_untouched(self, tmp_path):
        from camp.group.window_record import window_record_path_for
        from camp.launch.tmux import WindowListing
        from camp.launch.window_reconcile import NotReconciled, reconcile_workspace_record

        path = window_record_path_for(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json", encoding="utf-8")
        before = path.read_bytes()

        tmux = _StubTmux(WindowListing(windows=(), dropped=0))
        outcome = reconcile_workspace_record(tmp_path, "sess", tmux)

        assert isinstance(outcome, NotReconciled)
        assert outcome.reason == f"camp: window record at {path} could not be read; not reconciled"
        assert path.read_bytes() == before

    def test_unanswered_listing_yields_not_reconciled_with_no_write(self, tmp_path):
        from camp.group.window_record import window_record_path_for, write_window_record
        from camp.launch.tmux import UNANSWERED
        from camp.launch.window_reconcile import NotReconciled, reconcile_workspace_record

        entry = _entry("@1", "a", cwd="repo")
        path = window_record_path_for(tmp_path)
        write_window_record(path, [entry])
        before = path.read_bytes()

        tmux = _StubTmux(UNANSWERED)
        outcome = reconcile_workspace_record(tmp_path, "sess", tmux)

        assert isinstance(outcome, NotReconciled)
        assert path.read_bytes() == before

    def test_none_listing_yields_not_reconciled_with_no_write(self, tmp_path):
        from camp.group.window_record import window_record_path_for, write_window_record
        from camp.launch.window_reconcile import NotReconciled, reconcile_workspace_record

        entry = _entry("@1", "a", cwd="repo")
        path = window_record_path_for(tmp_path)
        write_window_record(path, [entry])
        before = path.read_bytes()

        tmux = _StubTmux(None)
        outcome = reconcile_workspace_record(tmp_path, "sess", tmux)

        assert isinstance(outcome, NotReconciled)
        assert path.read_bytes() == before

    def test_a_lock_held_by_another_process_yields_not_reconciled_and_leaves_the_record_untouched(
        self, tmp_path
    ):
        """The connect fold and the stop path take `reconcile_lock`, which
        the background provisioner can hold for a long time (across every
        member's `git worktree add`). `reconcile_workspace_record` bounds
        its own acquire so a caller stuck behind that lock reports "not
        reconciled" instead of hanging silently — the record is left
        exactly as it was."""
        import threading

        from camp.group.manifest import reconcile_lock
        from camp.group.window_record import window_record_path_for, write_window_record
        from camp.launch.tmux import WindowListing
        from camp.launch.window_reconcile import NotReconciled, reconcile_workspace_record

        entry = _entry("@1", "a", cwd="repo")
        path = window_record_path_for(tmp_path)
        write_window_record(path, [entry])
        before = path.read_bytes()

        holder_ready = threading.Event()
        release_holder = threading.Event()

        def hold():
            with reconcile_lock(tmp_path):
                holder_ready.set()
                release_holder.wait(timeout=5)

        holder = threading.Thread(target=hold)
        holder.start()
        assert holder_ready.wait(timeout=5)
        try:
            tmux = _StubTmux(WindowListing(windows=(_window("@1", "a"),), dropped=0))
            outcome = reconcile_workspace_record(tmp_path, "sess", tmux, lock_timeout=0.2)
        finally:
            release_holder.set()
            holder.join(timeout=5)

        assert isinstance(outcome, NotReconciled)
        assert outcome.reason.startswith(f"camp: window record at {path} ")
        assert "locked" in outcome.reason
        assert path.read_bytes() == before

    def test_a_dropped_listing_row_yields_not_reconciled_and_leaves_the_record_untouched(self, tmp_path):
        """A live window whose `list-windows` row failed to split (tmux
        refuses tabs in names only; a pane path can contain one) must never
        be read as "closed" — that would silently drop its recorded entry,
        conversation id included, for good. `reconcile_workspace_record`
        refuses instead, naming the path with the same prefix every other
        `NotReconciled` reason carries."""
        from camp.group.window_record import window_record_path_for, write_window_record
        from camp.launch.tmux import WindowListing
        from camp.launch.window_reconcile import NotReconciled, reconcile_workspace_record

        entry = _entry("@1", "a", cwd="repo")
        path = window_record_path_for(tmp_path)
        write_window_record(path, [entry])
        before = path.read_bytes()

        tmux = _StubTmux(WindowListing(windows=(_window("@1", "a"),), dropped=1))
        outcome = reconcile_workspace_record(tmp_path, "sess", tmux)

        assert isinstance(outcome, NotReconciled)
        assert outcome.reason.startswith(f"camp: window record at {path} ")
        assert "1" in outcome.reason
        assert path.read_bytes() == before

    def test_missing_record_with_no_windows_is_a_no_op(self, tmp_path):
        from camp.group.window_record import window_record_path_for
        from camp.launch.tmux import WindowListing
        from camp.launch.window_reconcile import Reconciled, reconcile_workspace_record

        tmux = _StubTmux(WindowListing(windows=(), dropped=0))

        outcome = reconcile_workspace_record(tmp_path, "sess", tmux)

        assert isinstance(outcome, Reconciled)
        assert outcome.changes == ()
        assert not window_record_path_for(tmp_path).exists()

    def test_no_backup_copy_is_written_alongside_a_reconciling_write(self, tmp_path):
        from camp.group.window_record import window_record_path_for, write_window_record
        from camp.launch.tmux import WindowListing
        from camp.launch.window_reconcile import reconcile_workspace_record

        ws_dir = tmp_path / "ws"
        entry = _entry("@1", "old-name", cwd="repo", conversation_id="c1")
        path = window_record_path_for(ws_dir)
        write_window_record(path, [entry])

        tmux = _StubTmux(WindowListing(windows=(_window("@1", "new-name"),), dropped=0))
        reconcile_workspace_record(ws_dir, "sess", tmux)

        assert sorted(p.name for p in ws_dir.iterdir()) == [path.name]


class TestRenderChanges:
    def test_no_changes_renders_nothing(self):
        from camp.launch.window_reconcile import render_changes

        assert render_changes(()) == []

    def test_dropped_entry_with_conversation_renders_its_id(self):
        from camp.launch.window_reconcile import Dropped, render_changes

        change = Dropped(window_id="@3", name="review", conversation_id="41aa1234")

        lines = render_changes([change])

        assert lines == ['camp: window record: dropped @3 "review" (closed in tmux; conversation 41aa1234)']

    def test_dropped_entry_with_command_line_renders_its_command_instead(self):
        from camp.launch.window_reconcile import Dropped, render_changes

        change = Dropped(window_id="@4", name="build", command_line="pytest")

        lines = render_changes([change])

        assert lines == ['camp: window record: dropped @4 "build" (closed in tmux; command pytest)']

    def test_renamed_entry_renders_old_and_new_name(self):
        from camp.launch.window_reconcile import Renamed, render_changes

        change = Renamed(window_id="@1", old="first", new="planning")

        lines = render_changes([change])

        assert lines == ['camp: window record: renamed @1 "first" -> "planning"']

    def test_control_characters_in_a_name_are_rendered_printable(self):
        from camp.launch.window_reconcile import Renamed, render_changes

        change = Renamed(window_id="@1", old="first", new="evil\r\x1b[2Kname")

        lines = render_changes([change])

        assert "\r" not in lines[0]
        assert "\x1b" not in lines[0]
        assert "\\x0d" in lines[0]
        assert "\\x1b" in lines[0]
