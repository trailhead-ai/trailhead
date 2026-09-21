"""Tests for launch/stop_workspace.py's `stop_workspace` engine — the
door's inverse: reconcile, preview, kill, poll.

Test contract (see
task/stopping-a-workspace-reconciles-previews-kills-and-checks):

1. Success arm: the kill argv reaches the seam exactly once, targeted
   ``=<session>``, after the preview was emitted and after the record was
   reconciled — order asserted on a shared call log.
2. `kill_session` is never called when `has_session` answers `False` or
   `None`, and the record file is untouched on both arms.
3. A fake tmux that answers `None` on the second poll yields
   `RefusedTmuxUnanswered`, and the record on disk is the reconciled one.
4. A fake tmux that keeps answering present after the kill yields
   `StillPresent` within the poll budget, and the record on disk is the
   reconciled one.
5. A corrupt record: the preview carries `reconciled=False` and the note
   names the path, the kill still happens, the file is untouched.
6. A window that disappears between the listing and the kill changes
   nothing about the outcome — the kill targets the session and the
   result is `Stopped`.
7. The preview rows are those of the listing taken after reconciliation,
   so a window dropped by reconciliation is not previewed.
8. `stop_workspace` and `stop_session` share one poll-deadline rule (a fake
   clock proves the same wall-clock bound holds for both).

Mirrors test_window_reconcile.py's convention: sys.path is set up for the
plugin package before any `camp.*` import, and every `camp.*` symbol is
imported inside the function that uses it.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _window(window_id, name, current_path="/ws", current_command="bash"):
    from camp.launch.tmux import TmuxWindow

    return TmuxWindow(window_id=window_id, current_path=current_path, current_command=current_command, name=name)


def _entry(window_id, name, cwd=".", conversation_id="conv", command_line=None):
    from camp.group.window_record import WindowEntry

    if command_line is not None:
        return WindowEntry(window_id=window_id, name=name, cwd=cwd, command_line=command_line)
    return WindowEntry(window_id=window_id, name=name, cwd=cwd, conversation_id=conversation_id)


def _write_record(ws_dir: Path, entries) -> None:
    from camp.group.window_record import window_record_path_for, write_window_record

    write_window_record(window_record_path_for(ws_dir), list(entries))


def _read_record(ws_dir: Path):
    from camp.group.window_record import read_window_record, window_record_path_for

    return read_window_record(window_record_path_for(ws_dir))


class _ScriptedTmux:
    """A fake `Tmux` whose `has_session` answers *initial* until killed, then
    walks *poll_sequence* (repeating the last entry once exhausted).

    Every call is appended to *calls* — a list shared with whatever else the
    test wants ordered against it (e.g. the `emit` sink), so ordering can be
    asserted across both seams on one shared log.
    """

    def __init__(
        self,
        *,
        initial: bool | None = True,
        listing_windows=(),
        poll_sequence=(False,),
        listing_sequence=(),
        calls: list | None = None,
    ) -> None:
        self.calls = calls if calls is not None else []
        # Scripted answers for successive `list_windows` calls, consumed
        # first; once exhausted, `listing_windows` answers every call.
        self._listing_sequence = list(listing_sequence)
        self._initial = initial
        self._listing_windows = tuple(listing_windows)
        self._poll_sequence = list(poll_sequence)
        self.killed: list[str] = []
        self.has_session_call_count = 0

    def has_session(self, name: str):
        self.has_session_call_count += 1
        if not self.killed:
            self.calls.append(("has_session:initial", name))
            return self._initial
        self.calls.append(("has_session:poll", name))
        if len(self._poll_sequence) > 1:
            return self._poll_sequence.pop(0)
        return self._poll_sequence[0]

    def list_windows(self, name: str):
        from camp.launch.tmux import WindowListing

        self.calls.append(("list_windows", name))
        if self._listing_sequence:
            return self._listing_sequence.pop(0)
        return WindowListing(windows=self._listing_windows)

    def kill_session(self, name: str):
        self.calls.append(("kill", name))
        self.killed.append(name)


def _stop_workspace(ws_dir: Path, tmux, *, emit=None, **kwargs):
    from camp.launch.stop_workspace import DEFAULT_SHELL_NAMES, stop_workspace

    lines: list[str] = []

    def _emit(line: str) -> None:
        lines.append(line)
        if emit is not None:
            emit(line)

    kwargs.setdefault("poll_timeout", 1.0)
    kwargs.setdefault("poll_interval", 0.01)
    kwargs.setdefault("shell_names", DEFAULT_SHELL_NAMES)
    outcome = stop_workspace(
        "g",
        "slug",
        ws_dir,
        tmux=tmux,
        emit=_emit,
        **kwargs,
    )
    return outcome, lines


# ---------------------------------------------------------------------------
# 1. Order: reconcile, then preview emitted, then the kill — on one call log
# ---------------------------------------------------------------------------


def test_kill_reaches_the_seam_once_targeted_after_preview_and_reconcile(tmp_path: Path) -> None:
    from camp.launch.naming import workspace_session_name
    from camp.launch.stop_workspace import Stopped

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    session = workspace_session_name("g", "slug")

    # A record entry whose window is gone in the live listing: reconciling
    # this drives a real change, so the "reconcile happened before preview"
    # half of the ordering claim has something to observe.
    _write_record(ws_dir, [_entry("@9", "gone"), _entry("@1", "main")])

    calls: list = []
    tmux = _ScriptedTmux(
        listing_windows=[_window("@1", "main", current_command="bash")],
        poll_sequence=[False],
        calls=calls,
    )

    outcome, emitted = _stop_workspace(ws_dir, tmux, emit=lambda line: calls.append(("emit", line)))

    assert isinstance(outcome, Stopped)
    assert tmux.killed == [session]

    kill_index = calls.index(("kill", session))
    emit_indexes = [i for i, c in enumerate(calls) if c[0] == "emit"]
    reconcile_note_index = min(i for i, c in enumerate(calls) if c[0] == "emit" and "dropped" in c[1])
    preview_index = min(i for i, c in enumerate(calls) if c[0] == "emit" and c[1].startswith("stopping "))

    assert reconcile_note_index < preview_index < kill_index
    assert all(i < kill_index for i in emit_indexes)
    # Kill reaches the seam exactly once.
    assert calls.count(("kill", session)) == 1


# ---------------------------------------------------------------------------
# 2. kill_session is never called on a not-running or unanswered workspace
# ---------------------------------------------------------------------------


def test_not_running_never_kills_and_leaves_the_record_untouched(tmp_path: Path) -> None:
    from camp.launch.stop_workspace import NotRunning

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    _write_record(ws_dir, [_entry("@1", "main")])
    before = _read_record(ws_dir)

    tmux = _ScriptedTmux(initial=False)

    outcome, _lines = _stop_workspace(ws_dir, tmux)

    assert isinstance(outcome, NotRunning)
    assert tmux.killed == []
    after = _read_record(ws_dir)
    assert after.entries == before.entries


def test_tmux_unanswered_up_front_never_kills_and_leaves_the_record_untouched(
    tmp_path: Path,
) -> None:
    from camp.launch.stop_workspace import RefusedTmuxUnanswered

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    _write_record(ws_dir, [_entry("@1", "main")])
    before = _read_record(ws_dir)

    tmux = _ScriptedTmux(initial=None)

    outcome, _lines = _stop_workspace(ws_dir, tmux)

    assert isinstance(outcome, RefusedTmuxUnanswered)
    assert tmux.killed == []
    after = _read_record(ws_dir)
    assert after.entries == before.entries


# ---------------------------------------------------------------------------
# 3. tmux goes quiet mid-poll -> refused, record left as reconciled
# ---------------------------------------------------------------------------


def test_tmux_unanswered_on_the_second_poll_refuses_with_the_reconciled_record_on_disk(
    tmp_path: Path,
) -> None:
    from camp.launch.stop_workspace import RefusedTmuxUnanswered

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    # This entry's window is gone live, so reconciliation writes a change —
    # the record "on disk" this test checks is the corrected one, not
    # whatever was there before the stop.
    _write_record(ws_dir, [_entry("@9", "gone"), _entry("@1", "main")])

    tmux = _ScriptedTmux(
        listing_windows=[_window("@1", "main")],
        poll_sequence=[True, None],
    )

    outcome, _lines = _stop_workspace(ws_dir, tmux)

    assert isinstance(outcome, RefusedTmuxUnanswered)
    on_disk = _read_record(ws_dir)
    assert [e.window_id for e in on_disk.entries] == ["@1"]


# ---------------------------------------------------------------------------
# 4. Session survives the kill -> StillPresent, record reconciled, entry gone
# ---------------------------------------------------------------------------


def test_session_survives_the_kill_within_the_poll_budget(tmp_path: Path) -> None:
    from camp.launch.stop_workspace import StillPresent

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    _write_record(ws_dir, [_entry("@9", "gone"), _entry("@1", "main")])

    tmux = _ScriptedTmux(
        listing_windows=[_window("@1", "main")],
        poll_sequence=[True],  # keeps answering present forever
    )

    outcome, _lines = _stop_workspace(ws_dir, tmux, poll_timeout=0.05, poll_interval=0.01)

    assert isinstance(outcome, StillPresent)
    on_disk = _read_record(ws_dir)
    # The closed entry ("@9") is gone; no entry was re-added for a window
    # that was never composed by camp.
    assert [e.window_id for e in on_disk.entries] == ["@1"]


# ---------------------------------------------------------------------------
# 5. Corrupt record: preview says so, kill still happens, file untouched
# ---------------------------------------------------------------------------


def test_a_corrupt_record_previews_unreconciled_and_still_kills(tmp_path: Path) -> None:
    from camp.group.window_record import window_record_path_for
    from camp.launch.stop_workspace import Stopped

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    path = window_record_path_for(ws_dir)
    path.write_text("{not json", encoding="utf-8")
    before = path.read_bytes()

    tmux = _ScriptedTmux(listing_windows=[_window("@1", "main")], poll_sequence=[False])

    outcome, _lines = _stop_workspace(ws_dir, tmux)

    assert isinstance(outcome, Stopped)
    assert outcome.preview.reconciled is False
    assert outcome.preview.reconcile_note is not None
    assert str(path) in outcome.preview.reconcile_note
    assert tmux.killed
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# 6. A window disappears between the listing and the kill -> unaffected
# ---------------------------------------------------------------------------


def test_a_window_disappearing_before_the_kill_still_reports_stopped(tmp_path: Path) -> None:
    from camp.launch.naming import workspace_session_name
    from camp.launch.stop_workspace import Stopped

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    session = workspace_session_name("g", "slug")

    tmux = _ScriptedTmux(
        listing_windows=[_window("@1", "main"), _window("@2", "extra")],
        poll_sequence=[False],
    )

    outcome, _lines = _stop_workspace(ws_dir, tmux)

    assert isinstance(outcome, Stopped)
    assert tmux.killed == [session]
    # The kill targets the whole session, never a window — the preview
    # snapshot from before the kill is what's reported. `list_windows` is
    # called exactly twice (once inside reconciliation, once for the fresh
    # preview listing) and never again after the kill.
    list_windows_calls = [c for c in tmux.calls if c[0] == "list_windows"]
    assert list_windows_calls == [("list_windows", session), ("list_windows", session)]
    kill_index = tmux.calls.index(("kill", session))
    assert all(tmux.calls.index(c) < kill_index for c in list_windows_calls)


# ---------------------------------------------------------------------------
# 7. Preview rows come from the listing taken AFTER reconciliation
# ---------------------------------------------------------------------------


def test_a_window_dropped_by_reconciliation_is_not_previewed(tmp_path: Path) -> None:
    from camp.launch.stop_workspace import Stopped

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    # Recorded, but no longer live: reconciliation drops it before the
    # preview's own listing runs.
    _write_record(ws_dir, [_entry("@9", "gone"), _entry("@1", "main")])

    tmux = _ScriptedTmux(
        listing_windows=[_window("@1", "main")],  # "@9" is already gone live
        poll_sequence=[False],
    )

    outcome, _lines = _stop_workspace(ws_dir, tmux)

    assert isinstance(outcome, Stopped)
    assert [row.window_id for row in outcome.preview.windows] == ["@1"]


# ---------------------------------------------------------------------------
# 8. stop_workspace and stop_session share one poll-deadline rule
# ---------------------------------------------------------------------------


def test_stop_workspace_and_stop_session_share_one_poll_deadline_rule(tmp_path: Path) -> None:
    from camp.launch import stop
    from camp.launch.stop_workspace import StillPresent, stop_workspace

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    clock = {"now": 0.0}

    class _SlowToAnswer:
        def has_session(self, name: str) -> bool:
            clock["now"] += stop.TMUX_TIMEOUT_SECONDS
            return True

        def list_windows(self, name: str):
            from camp.launch.tmux import WindowListing

            return WindowListing(windows=())

        def kill_session(self, name: str) -> None:
            return None

    def _sleep(seconds: float) -> None:
        clock["now"] += seconds

    outcome = stop_workspace(
        "g",
        "slug",
        ws_dir,
        tmux=_SlowToAnswer(),
        poll_timeout=stop.POLL_TIMEOUT_SECONDS,
        poll_interval=stop.POLL_INTERVAL_SECONDS,
        emit=lambda line: None,
        sleep=_sleep,
        monotonic=lambda: clock["now"],
    )

    assert isinstance(outcome, StillPresent)
    # The exact bound `test_the_re_poll_is_bounded_in_wall_clock_not_in_sleep_time`
    # pins for `stop_session` — proving one shared deadline rule, not two that
    # happen to agree today.
    assert clock["now"] <= stop.POLL_TIMEOUT_SECONDS + 2 * stop.TMUX_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# 9. A dropped listing row during reconciliation refuses to reconcile, but
#    the kill still proceeds and the record is left untouched.
# ---------------------------------------------------------------------------


def test_a_dropped_listing_row_during_reconcile_still_kills_and_leaves_the_record(tmp_path: Path) -> None:
    from camp.launch.tmux import WindowListing
    from camp.launch.stop_workspace import Stopped

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    _write_record(ws_dir, [_entry("@1", "main")])
    before = _read_record(ws_dir)

    tmux = _ScriptedTmux(
        listing_sequence=[WindowListing(windows=(_window("@1", "main"),), dropped=1)],
        listing_windows=[_window("@1", "main")],
        poll_sequence=[False],
    )

    outcome, lines = _stop_workspace(ws_dir, tmux)

    assert isinstance(outcome, Stopped)
    assert tmux.killed
    after = _read_record(ws_dir)
    assert after.entries == before.entries
    assert any(line.startswith("camp: window record at ") and "not reconciled" in line for line in lines)


# ---------------------------------------------------------------------------
# 10. tmux does not answer the preview listing -> no count line, kill proceeds
# ---------------------------------------------------------------------------


def test_an_unanswered_preview_listing_is_never_read_as_no_windows(tmp_path: Path) -> None:
    """The reconciliation's listing answered; the preview's own, a moment
    later, did not. "Could not tell" must not print as "0 windows": the
    preview says the windows could not be listed, and the stop still
    proceeds, because the kill is what the operator asked for."""
    from camp.launch.stop_workspace import Stopped
    from camp.launch.tmux import UNANSWERED, WindowListing

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    _write_record(ws_dir, [_entry("@1", "main")])

    answered = WindowListing(windows=(_window("@1", "main", current_command="pytest"),))
    for second_answer in (UNANSWERED, None):
        tmux = _ScriptedTmux(listing_sequence=[answered, second_answer])

        outcome, lines = _stop_workspace(ws_dir, tmux)

        assert isinstance(outcome, Stopped), second_answer
        assert tmux.killed == ["=camp-g-slug"] or tmux.killed == ["camp-g-slug"], tmux.killed
        assert not any("0 windows" in line for line in lines), (second_answer, lines)
        assert any("could not list" in line and "camp-g-slug" in line for line in lines), (second_answer, lines)
