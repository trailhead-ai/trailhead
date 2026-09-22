"""Tests for camp.launch.resurrect.resurrect_workspace_session — the
engine that drives tmux from the pure planner's decisions and re-stamps
the window record with what tmux actually created.

Test contract (task/the-resurrection-engine-creates-the-windows-in-order-and-isolates-each-failure):

1. Three restorable entries: one `new_session_with_window` call carrying the
   first entry's name, directory, and stub, then two `new_window` calls in
   record order; the record afterwards holds the three ids the fake handed
   back, in order; `restored` has three, `failed` and `dropped` none.
2. The fake answers `None`/`UNANSWERED` for the second `new_window`: the
   third window is still created, `failed` holds the second entry, the
   record holds the first and third only, and the failure line carries the
   second's conversation id.
3. A record where every entry's directory is gone: `create_workspace_session`'s
   path is taken (the fake sees a plain `new_session`), `dropped` holds all,
   the record is empty afterwards.
4. `DUPLICATE` from the first call returns `DuplicateSession` and no
   `new_window` call is made; a failure returns `CreateFailed` carrying the
   stderr and touches the record not at all.
5. The three `@camp_*` `set_option` calls and `install_window_binding` are
   made exactly once on success; a refused `set_option` kills the session
   and returns `CreateFailed`.
6. Restamp lock timeout: the result's restamp outcome is `NotRestamped` and
   the rendered lines include the could-not-be-re-stamped line, while
   `restored` still lists the windows.

Every test drives `resurrect_workspace_session` against a hand-rolled fake
`Tmux` that records what it was called with — never a real tmux — and a
real window record on `tmp_path`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.group.window_record import (  # noqa: E402
    WindowEntry,
    read_window_record,
    window_record_path_for,
    write_window_record,
)
from camp.launch.resurrect import (  # noqa: E402
    CreateFailed,
    DuplicateSession,
    Failed,
    ResurrectionResult,
    render_resurrection_lines,
    resurrect_workspace_session,
)
from camp.launch.tmux import (  # noqa: E402
    DUPLICATE,
    UNANSWERED,
    NewSessionWindowFailure,
    NewWindowResult,
)


class _FakeTmux:
    """Records every call this engine can make and answers with fixed,
    configurable results — mirrors `_FakeTmux` in
    `test_launch_workspace_session.py`, extended with the two
    session/window-creating calls the engine drives."""

    def __init__(
        self,
        *,
        first_window=None,
        window_answers=(),
        new_session_returncode: int = 0,
        new_session_stderr: str = "",
        failing_option: str | None = None,
    ) -> None:
        self._first_window = first_window
        self._window_answers = list(window_answers)
        self._new_session_returncode = new_session_returncode
        self._new_session_stderr = new_session_stderr
        self._failing_option = failing_option
        self.new_session_with_window_calls: list[dict[str, object]] = []
        self.new_window_calls: list[dict[str, object]] = []
        self.new_session_calls: list[dict[str, object]] = []
        self.set_option_calls: list[dict[str, object]] = []
        self.install_binding_calls: list[str] = []
        self.killed: list[str] = []

    def new_session_with_window(self, name, *, cwd, window_name, command, env=None, timeout=None):
        self.new_session_with_window_calls.append(
            {"name": name, "cwd": cwd, "window_name": window_name, "command": command}
        )
        return self._first_window

    def new_window(self, name, *, cwd, window_name, command, timeout=None):
        self.new_window_calls.append(
            {"name": name, "cwd": cwd, "window_name": window_name, "command": command}
        )
        return self._window_answers.pop(0)

    def new_session(self, name, *, cwd, env=None, timeout=None):
        self.new_session_calls.append({"name": name, "cwd": cwd})
        return subprocess.CompletedProcess(
            args=["tmux"],
            returncode=self._new_session_returncode,
            stdout="",
            stderr=self._new_session_stderr,
        )

    def set_option(self, target, key, value, *, timeout=None):
        self.set_option_calls.append({"target": target, "key": key, "value": value})
        if key == self._failing_option:
            return subprocess.CompletedProcess(
                args=["tmux"], returncode=1, stdout="", stderr="tmux: refused"
            )
        return subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr="")

    def kill_session(self, name, *, timeout=None):
        self.killed.append(name)
        return subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr="")

    def list_window_binding(self):
        return None

    def install_window_binding(self, true_command, *, timeout=None):
        self.install_binding_calls.append(true_command)
        return subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr="")


def _mkws(tmp_path) -> Path:
    ws = tmp_path / "ws"
    for member in ("one", "two", "three"):
        (ws / member).mkdir(parents=True)
    return ws


def _entries() -> tuple[WindowEntry, WindowEntry, WindowEntry]:
    e1 = WindowEntry(window_id="@1", name="one", cwd="one", conversation_id="c1")
    e2 = WindowEntry(window_id="@2", name="two", cwd="two", conversation_id="c2")
    e3 = WindowEntry(window_id="@3", name="three", cwd="three", conversation_id="c3")
    return e1, e2, e3


def _env(tmp_path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"HOME": str(home)}


# -- 1. three restorable entries, full success -------------------------------


def test_three_restorable_entries_ride_new_session_with_window_then_two_new_windows(
    tmp_path,
):
    ws = _mkws(tmp_path)
    e1, e2, e3 = _entries()
    path = window_record_path_for(ws)
    write_window_record(path, [e1, e2, e3])

    tmux = _FakeTmux(
        first_window=NewWindowResult(window_id="@10", window_name="one"),
        window_answers=[
            NewWindowResult(window_id="@11", window_name="two"),
            NewWindowResult(window_id="@12", window_name="three"),
        ],
    )

    result = resurrect_workspace_session(
        "trailhead", "camp-cli", ws, [e1, e2, e3], env=_env(tmp_path), tmux=tmux, harness=None
    )

    assert isinstance(result, ResurrectionResult)
    assert len(tmux.new_session_with_window_calls) == 1
    first_call = tmux.new_session_with_window_calls[0]
    assert first_call["window_name"] == "one"
    assert Path(first_call["cwd"]) == (ws / "one").resolve()

    assert [c["window_name"] for c in tmux.new_window_calls] == ["two", "three"]

    assert result.restored == (
        WindowEntry(window_id="@10", name="one", cwd="one", conversation_id="c1"),
        WindowEntry(window_id="@11", name="two", cwd="two", conversation_id="c2"),
        WindowEntry(window_id="@12", name="three", cwd="three", conversation_id="c3"),
    )
    assert result.failed == ()
    assert result.dropped == ()

    record = read_window_record(path)
    assert record.entries == result.restored


# -- 2. one window fails, the rest still come back ---------------------------


@pytest.mark.parametrize("bad_answer", [None, UNANSWERED])
def test_a_failed_window_is_isolated_and_the_rest_still_come_back(tmp_path, bad_answer):
    ws = _mkws(tmp_path)
    e1, e2, e3 = _entries()
    path = window_record_path_for(ws)
    write_window_record(path, [e1, e2, e3])

    tmux = _FakeTmux(
        first_window=NewWindowResult(window_id="@10", window_name="one"),
        window_answers=[bad_answer, NewWindowResult(window_id="@12", window_name="three")],
    )

    result = resurrect_workspace_session(
        "trailhead", "camp-cli", ws, [e1, e2, e3], env=_env(tmp_path), tmux=tmux, harness=None
    )

    assert len(tmux.new_window_calls) == 2, "the third window must still be attempted"
    assert result.failed == (Failed(e2, result.failed[0].reason),)
    assert result.dropped == ()

    record = read_window_record(path)
    assert [e.window_id for e in record.entries] == ["@10", "@12"]

    lines = render_resurrection_lines(result)
    assert any("c2" in line for line in lines), lines


# -- 3. every directory gone: the create arm, record ends empty --------------


def test_every_entry_dropped_takes_the_create_arm_and_empties_the_record(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    e1 = WindowEntry(window_id="@1", name="one", cwd="gone-one", conversation_id="c1")
    e2 = WindowEntry(window_id="@2", name="two", cwd="gone-two", conversation_id="c2")
    e3 = WindowEntry(window_id="@3", name="three", cwd="gone-three", conversation_id="c3")
    path = window_record_path_for(ws)
    write_window_record(path, [e1, e2, e3])

    tmux = _FakeTmux()

    result = resurrect_workspace_session(
        "trailhead", "camp-cli", ws, [e1, e2, e3], env=_env(tmp_path), tmux=tmux, harness=None
    )

    assert isinstance(result, ResurrectionResult)
    assert len(tmux.new_session_calls) == 1, "the plain create arm must be taken"
    assert tmux.new_session_with_window_calls == []
    assert tmux.new_window_calls == []
    assert result.restored == ()
    assert len(result.dropped) == 3

    record = read_window_record(path)
    assert record.entries == ()


# -- 4. DUPLICATE and a plain failure on the first call -----------------------


def test_duplicate_on_the_first_call_folds_to_duplicate_session_with_no_new_window_call(
    tmp_path,
):
    ws = _mkws(tmp_path)
    e1, e2, e3 = _entries()
    path = window_record_path_for(ws)
    write_window_record(path, [e1, e2, e3])

    tmux = _FakeTmux(first_window=DUPLICATE)

    result = resurrect_workspace_session(
        "trailhead", "camp-cli", ws, [e1, e2, e3], env=_env(tmp_path), tmux=tmux, harness=None
    )

    assert isinstance(result, DuplicateSession)
    assert tmux.new_window_calls == []
    record = read_window_record(path)
    assert record.entries == (e1, e2, e3), "the record must be untouched"


def test_a_failed_first_call_returns_create_failed_and_touches_the_record_not_at_all(
    tmp_path,
):
    ws = _mkws(tmp_path)
    e1, e2, e3 = _entries()
    path = window_record_path_for(ws)
    write_window_record(path, [e1, e2, e3])

    tmux = _FakeTmux(first_window=NewSessionWindowFailure(stderr="tmux: boom\n"))

    result = resurrect_workspace_session(
        "trailhead", "camp-cli", ws, [e1, e2, e3], env=_env(tmp_path), tmux=tmux, harness=None
    )

    assert isinstance(result, CreateFailed)
    assert result.error == "tmux: boom\n"
    record = read_window_record(path)
    assert record.entries == (e1, e2, e3)


# -- 5. mark-and-bind exactly once; a refused option kills and fails ---------


def test_the_three_camp_options_and_the_binding_are_set_exactly_once_on_success(tmp_path):
    ws = _mkws(tmp_path)
    e1, e2, e3 = _entries()
    path = window_record_path_for(ws)
    write_window_record(path, [e1, e2, e3])

    tmux = _FakeTmux(
        first_window=NewWindowResult(window_id="@10", window_name="one"),
        window_answers=[
            NewWindowResult(window_id="@11", window_name="two"),
            NewWindowResult(window_id="@12", window_name="three"),
        ],
    )

    resurrect_workspace_session(
        "trailhead", "camp-cli", ws, [e1, e2, e3], env=_env(tmp_path), tmux=tmux, harness=None
    )

    options = {c["key"]: c["value"] for c in tmux.set_option_calls}
    assert options == {"@camp_workspace": "1", "@camp_group": "trailhead", "@camp_slug": "camp-cli"}
    assert len(tmux.set_option_calls) == 3, "each option must be set exactly once"
    assert len(tmux.install_binding_calls) == 1


def test_a_refused_option_kills_the_session_and_returns_create_failed(tmp_path):
    ws = _mkws(tmp_path)
    e1, e2, e3 = _entries()
    path = window_record_path_for(ws)
    write_window_record(path, [e1, e2, e3])

    tmux = _FakeTmux(
        first_window=NewWindowResult(window_id="@10", window_name="one"),
        failing_option="@camp_group",
    )

    result = resurrect_workspace_session(
        "trailhead", "camp-cli", ws, [e1, e2, e3], env=_env(tmp_path), tmux=tmux, harness=None
    )

    assert isinstance(result, CreateFailed)
    assert tmux.killed, "the half-marked session must be killed"
    assert tmux.new_window_calls == [], "no later window is attempted once marking fails"
    record = read_window_record(path)
    assert record.entries == (e1, e2, e3), "the record must be untouched"


# -- Folded in from Task 4 review: the restamp-failure line must not double
#    the "camp:" prefix or the record path -------------------------------------


def test_the_restamp_failure_line_names_the_underlying_reason_once_not_doubled(tmp_path):
    """`restamp_window_entries`'s own `NotRestamped.reason` on a lock
    timeout already spells `camp: could not restamp window record at
    <path>: <e>` (`group/window_record.py:357`) — composing THAT into
    `render_resurrection_lines`'s own template doubled the `camp:` prefix
    and the path. Varied across two distinct underlying reasons: the exact
    shape `restamp_window_entries` emits on a lock timeout (which carries
    the redundant prefix), and a plain reason with no such prefix (proving
    the fix does not merely special-case one literal string)."""
    from camp.group.window_record import NotRestamped
    from camp.launch.resurrect import ResurrectionResult, render_resurrection_lines

    path = tmp_path / "windows.json"

    def _result(restamp):
        return ResurrectionResult(
            session_name="camp-trailhead-camp-cli",
            restored=(),
            failed=(),
            dropped=(),
            restamp=restamp,
            record_path=path,
        )

    prefixed = NotRestamped(
        reason=f"camp: could not restamp window record at {path}: locked by another camp process"
    )
    lines = render_resurrection_lines(_result(prefixed))
    assert lines[-1] == (
        f"camp: window record at {path} could not be re-stamped — "
        "locked by another camp process"
    )
    assert lines[-1].count("camp:") == 1
    assert lines[-1].count(str(path)) == 1

    bare = NotRestamped(reason="disk full")
    lines_bare = render_resurrection_lines(_result(bare))
    assert lines_bare[-1] == f"camp: window record at {path} could not be re-stamped — disk full"


# -- 6. restamp lock timeout ---------------------------------------------------


def test_a_restamp_failure_reports_not_restamped_but_keeps_the_restored_windows(
    tmp_path, monkeypatch
):
    """The lock-timeout / corrupt-at-restamp-time behaviour of
    `restamp_window_entries` itself is Task 3's own contract, already
    pinned against a REAL held lock in
    `test_window_record.py::TestRestampWindowEntries::test_lock_held_by_another_process_times_out_and_leaves_record_unchanged`
    (which this task's widened run includes). What THIS engine owns is
    forwarding whatever `restamp_window_entries` answers — so the fake here
    stands in for a `NotRestamped` answer directly, rather than paying for a
    second real subprocess-held flock just to reach the same outcome type.
    """
    import camp.launch.resurrect as resurrect_module
    from camp.group.window_record import NotRestamped

    ws = _mkws(tmp_path)
    e1, e2, e3 = _entries()
    path = window_record_path_for(ws)
    write_window_record(path, [e1, e2, e3])

    not_restamped = NotRestamped(
        reason=f"camp: could not restamp window record at {path}: locked by another camp process"
    )
    monkeypatch.setattr(
        resurrect_module, "restamp_window_entries", lambda *args, **kwargs: not_restamped
    )

    tmux = _FakeTmux(
        first_window=NewWindowResult(window_id="@10", window_name="one"),
        window_answers=[
            NewWindowResult(window_id="@11", window_name="two"),
            NewWindowResult(window_id="@12", window_name="three"),
        ],
    )

    result = resurrect_workspace_session(
        "trailhead", "camp-cli", ws, [e1, e2, e3], env=_env(tmp_path), tmux=tmux, harness=None
    )

    assert result.restamp is not_restamped
    assert len(result.restored) == 3, "the windows tmux actually created must still be reported"

    lines = render_resurrection_lines(result)
    assert any("could not be re-stamped" in line for line in lines), lines
