"""Tests for launch/conversation_capture.py — recording the conversation a
harness session-start hook reports against the camp workspace window it
started in.

Driven against the REAL `ClaudeCodeHarness` (a group with no `[harness]`
block resolves `binary = "claude"`) reading a real SessionStart payload, a
real window record on disk under a sandboxed state dir, and a hand-rolled
tmux stand-in answering the two questions the capture asks tmux: where the
pane sits, and what the session it sits in is marked with.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


class _Tmux:
    """Answers `pane_window` from a pane -> PaneWindow map and `show_option`
    from a session id -> {option: value} map; records every call."""

    def __init__(self, panes: dict, options: dict) -> None:
        self._panes = panes
        self._options = options
        self.calls: list[tuple] = []

    def pane_window(self, pane, *, timeout=None):
        self.calls.append(("pane_window", pane))
        return self._panes.get(pane)

    def show_option(self, target, key, *, timeout=None):
        self.calls.append(("show_option", target, key))
        return self._options.get(target, {}).get(key)


def _payload(session_id: str | None) -> str:
    body: dict = {"hook_event_name": "SessionStart", "source": "startup"}
    if session_id is not None:
        body["session_id"] = session_id
    return json.dumps(body)


@pytest.fixture
def workspace(tmp_path):
    """A group `g` with workspace `feat` on disk under a sandboxed state
    dir, with one member directory inside it."""
    from camp.group.manifest import workspace_dir

    config_dir = tmp_path / "config"
    (config_dir / "groups").mkdir(parents=True)
    env = {
        "CAMP_CONFIG_DIR": str(config_dir),
        "CAMP_STATE_DIR": str(tmp_path / "state"),
    }
    ws = workspace_dir("g", "feat", env=env)
    (ws / "member").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    env["HOME"] = str(home)
    group = {"group": {"name": "g"}, "members": []}
    return ws, env, group


def _marked(session_id: str = "$1", *, group: str = "g", slug: str = "feat") -> dict:
    return {session_id: {"@camp_workspace": "1", "@camp_group": group, "@camp_slug": slug}}


def _pane(window_id: str, cwd: Path, *, session_id: str = "$1", name: str = "claude"):
    from camp.launch.tmux import PaneWindow

    return PaneWindow(
        window_id=window_id, session_id=session_id, current_path=str(cwd), window_name=name
    )


def _capture(payload, *, pane, tmux, group, env):
    from camp.launch.conversation_capture import capture_conversation

    return capture_conversation(payload, pane=pane, tmux=tmux, all_configs=[group], env=env)


def _recorded(ws):
    from camp.group.window_record import read_window_record, window_record_path_for

    return read_window_record(window_record_path_for(ws)).entries


@pytest.mark.parametrize(
    ("window_id", "subdir", "session_id"),
    [("@3", "member", "0f6c2d1e-aaaa-4bbb-8ccc-123456789abc"), ("@9", ".", "sess-2")],
)
def test_a_conversation_is_recorded_against_the_window_it_started_in(
    workspace, window_id, subdir, session_id
):
    from camp.group.window_record import WindowEntry

    ws, env, group = workspace
    tmux = _Tmux({"%5": _pane(window_id, ws / subdir)}, _marked())

    entry = _capture(_payload(session_id), pane="%5", tmux=tmux, group=group, env=env)

    expected = WindowEntry(
        window_id=window_id, name="claude", cwd=subdir, conversation_id=session_id
    )
    assert entry == expected
    assert _recorded(ws) == (expected,)


def test_a_later_conversation_in_the_same_window_replaces_the_earlier_one(workspace):
    """A resume or a cleared context starts the window on a different
    conversation; resurrecting the earlier one would bring back a window
    the operator had already moved on from."""
    ws, env, group = workspace
    tmux = _Tmux({"%5": _pane("@3", ws / "member")}, _marked())

    _capture(_payload("first-conversation"), pane="%5", tmux=tmux, group=group, env=env)
    _capture(_payload("second-conversation"), pane="%5", tmux=tmux, group=group, env=env)

    assert [e.conversation_id for e in _recorded(ws)] == ["second-conversation"]


def test_a_session_started_outside_tmux_records_nothing_and_asks_tmux_nothing(workspace):
    ws, env, group = workspace
    tmux = _Tmux({}, {})

    entry = _capture(_payload("sess-1"), pane=None, tmux=tmux, group=group, env=env)

    assert entry is None
    assert tmux.calls == []
    assert _recorded(ws) == ()


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"$1": {"@camp_group": "g", "@camp_slug": "feat"}},
        {"$1": {"@camp_workspace": "1", "@camp_group": "g"}},
        {"$1": {"@camp_workspace": "1", "@camp_group": "other-group", "@camp_slug": "feat"}},
        {"$1": {"@camp_workspace": "1", "@camp_group": "g", "@camp_slug": "../escape"}},
    ],
    ids=["plain-session", "no-mark", "no-slug", "unknown-group", "unsafe-slug"],
)
def test_a_pane_outside_a_marked_camp_workspace_session_records_nothing(workspace, options):
    ws, env, group = workspace
    tmux = _Tmux({"%5": _pane("@3", ws / "member")}, options)

    entry = _capture(_payload("sess-1"), pane="%5", tmux=tmux, group=group, env=env)

    assert entry is None
    assert _recorded(ws) == ()


def test_a_pane_tmux_cannot_place_records_nothing(workspace):
    ws, env, group = workspace
    tmux = _Tmux({}, _marked())

    assert _capture(_payload("sess-1"), pane="%5", tmux=tmux, group=group, env=env) is None
    assert _recorded(ws) == ()


@pytest.mark.parametrize("payload", ["", "not json", json.dumps({"session_id": "a b"})])
def test_a_payload_naming_no_usable_session_records_nothing(workspace, payload):
    ws, env, group = workspace
    tmux = _Tmux({"%5": _pane("@3", ws / "member")}, _marked())

    assert _capture(payload, pane="%5", tmux=tmux, group=group, env=env) is None
    assert _recorded(ws) == ()


def test_a_conversation_started_outside_the_workspace_records_nothing(workspace, tmp_path):
    ws, env, group = workspace
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    tmux = _Tmux({"%5": _pane("@3", outside)}, _marked())

    assert _capture(_payload("sess-1"), pane="%5", tmux=tmux, group=group, env=env) is None
    assert _recorded(ws) == ()


def test_a_conversation_started_at_a_credential_store_records_nothing(workspace):
    """HOME is moved inside a member directory, so `~/.ssh` is a directory
    inside the workspace a pane can actually be rooted at."""
    ws, env, group = workspace
    home = ws / "member" / "home"
    (home / ".ssh").mkdir(parents=True)
    env = {**env, "HOME": str(home)}
    tmux = _Tmux({"%5": _pane("@3", home / ".ssh")}, _marked())

    assert _capture(_payload("sess-1"), pane="%5", tmux=tmux, group=group, env=env) is None
    assert _recorded(ws) == ()


def test_an_unreadable_record_is_left_as_it_is(workspace):
    """A record camp cannot parse is not one it may overwrite: the entries
    in it are the only copy of the conversations it describes."""
    from camp.group.window_record import window_record_path_for

    ws, env, group = workspace
    path = window_record_path_for(ws)
    path.write_text("{not json", encoding="utf-8")
    tmux = _Tmux({"%5": _pane("@3", ws / "member")}, _marked())

    assert _capture(_payload("sess-1"), pane="%5", tmux=tmux, group=group, env=env) is None
    assert path.read_text(encoding="utf-8") == "{not json"
