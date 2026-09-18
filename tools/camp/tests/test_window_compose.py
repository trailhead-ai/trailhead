"""Tests for camp.launch.window_compose — the verb that composes a window
inside a workspace's tmux session and records it (AC19, AC20).

Test contract (see
task/composing-a-window-camp-chooses-the-conversation-id-and-records-it-at-creation):

1. The recorded conversation id is the one camp passed to the harness — vary
   it and the recorded value follows.
2. The recorded working directory is the workspace-relative form of where
   the window was actually rooted; vary the directory and the recorded
   value follows.
3. The recorded tmux window id is the one the seam reported, not a
   predicted or sequential value.
4. The recorded name is the window's actual name in tmux, not a value
   recorded in parallel with whatever tmux was told — vary the name and the
   recorded value follows; the name camp recorded and the name tmux holds
   cannot diverge.
5. A window created with an explicit command records that command line and
   no conversation id; a window created as a conversation records the id
   and no command line. Both directions pinned.
6. The composed argv contains neither the remote-control flag nor the
   visible-name flag.
7. The scrub appears inside the command the window runs, and the tmux
   request that creates the window carries no scrub — asserted on both
   halves.
8. The record is written before the invocation reports success, and a
   window is recorded exactly once per creation.

Every test drives `compose_window` against a fake `Tmux` double (so the
argv `new_window` is called with, and what it echoes back, are both fully
controlled) and a fake harness (so the scrub set is controlled) — never a
real subprocess. `camp.launch.tmux`'s own suite pins the real argv/parse
against a `subprocess.run` stand-in and (manually, this task) against a real
tmux 3.7c binary; this file pins only the composition logic layered on top.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

GROUP = {"group": {"name": "testgroup"}}


class FakeHarness:
    """Stand-in for the trailhead harness seam — only the scrub method this
    verb reads."""

    def __init__(self, scrub=("SCRUB_ONE", "SCRUB_TWO")):
        self._scrub = tuple(scrub)

    def session_launch_env_unset(self):
        return list(self._scrub)


class FakeTmux:
    """Stand-in for `camp.launch.tmux.Tmux` — records every `new_window`
    call verbatim and answers with a canned (or diverging) result."""

    def __init__(self, *, window_id="@1", window_name_override=None):
        self._window_id = window_id
        self._window_name_override = window_name_override
        self.calls: list[dict] = []

    def new_window(self, name, *, cwd, window_name, command, env=None, timeout=None):
        from camp.launch.tmux import NewWindowResult

        self.calls.append(
            {
                "name": name,
                "cwd": cwd,
                "window_name": window_name,
                "command": list(command),
                "env": env,
            }
        )
        actual_name = (
            self._window_name_override
            if self._window_name_override is not None
            else window_name
        )
        return NewWindowResult(window_id=self._window_id, window_name=actual_name)


def _recorded_entries(ws_dir: Path):
    from camp.group.window_record import read_window_record, window_record_path_for

    return read_window_record(window_record_path_for(ws_dir)).entries


@pytest.fixture(autouse=True)
def _fake_harness(monkeypatch):
    import camp.launch.window_compose as wc

    monkeypatch.setattr(wc, "harness_for", lambda group: FakeHarness())


# ---------------------------------------------------------------------------
# 1. Conversation id
# ---------------------------------------------------------------------------


def test_recorded_conversation_id_is_the_one_passed_to_the_harness(tmp_path, monkeypatch):
    import camp.launch.window_compose as wc

    ids = iter(["conv-aaaa", "conv-bbbb"])
    monkeypatch.setattr(wc.uuid, "uuid4", lambda: next(ids))

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    tmux1 = FakeTmux()
    result1 = wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1", tmux=tmux1
    )
    tmux2 = FakeTmux()
    result2 = wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w2", tmux=tmux2
    )

    # The id recorded is the id that reached the composed argv — not two
    # independently-generated values that merely happen to agree.
    assert "conv-aaaa" in tmux1.calls[0]["command"]
    assert "conv-bbbb" in tmux2.calls[0]["command"]
    assert result1.conversation_id == "conv-aaaa"
    assert result2.conversation_id == "conv-bbbb"
    assert result1.conversation_id != result2.conversation_id

    entries = _recorded_entries(ws_dir)
    assert entries[0].conversation_id == "conv-aaaa"
    assert entries[1].conversation_id == "conv-bbbb"


# ---------------------------------------------------------------------------
# 2. Working directory
# ---------------------------------------------------------------------------


def test_recorded_cwd_is_the_workspace_relative_form_of_the_actual_root(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    (ws_dir / "sub_a").mkdir(parents=True)
    (ws_dir / "sub_b").mkdir(parents=True)

    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir / "sub_a", window_name="w1", tmux=FakeTmux()
    )
    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir / "sub_b", window_name="w2", tmux=FakeTmux()
    )

    entries = _recorded_entries(ws_dir)
    assert entries[0].cwd == "sub_a"
    assert entries[1].cwd == "sub_b"
    assert entries[0].cwd != entries[1].cwd


# ---------------------------------------------------------------------------
# 3. tmux window id
# ---------------------------------------------------------------------------


def test_recorded_window_id_is_the_one_the_seam_reported(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1",
        tmux=FakeTmux(window_id="@42"),
    )
    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w2",
        tmux=FakeTmux(window_id="@99"),
    )

    entries = _recorded_entries(ws_dir)
    assert entries[0].window_id == "@42"
    assert entries[1].window_id == "@99"


# ---------------------------------------------------------------------------
# 4. Window name — read back, cannot diverge
# ---------------------------------------------------------------------------


def test_recorded_name_follows_the_requested_name_when_tmux_honors_it(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="alpha", tmux=FakeTmux()
    )
    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="beta", tmux=FakeTmux()
    )

    entries = _recorded_entries(ws_dir)
    assert entries[0].name == "alpha"
    assert entries[1].name == "beta"


def test_recorded_name_is_tmuxs_answer_not_the_requested_name_when_they_diverge(
    tmp_path,
):
    """tmux is free to alter a requested name (e.g. de-duplication); the
    recorded name must be what tmux reported back, never the request."""
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    tmux = FakeTmux(window_name_override="alpha (1)")
    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="alpha", tmux=tmux
    )

    entries = _recorded_entries(ws_dir)
    assert entries[0].name == "alpha (1)"
    assert entries[0].name != "alpha"


# ---------------------------------------------------------------------------
# 5. Explicit command vs. conversation — both directions
# ---------------------------------------------------------------------------


def test_explicit_command_records_the_command_line_and_no_conversation_id(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1",
        command=["sleep", "30"], tmux=FakeTmux(),
    )

    entries = _recorded_entries(ws_dir)
    assert entries[0].conversation_id is None
    assert entries[0].command_line == "sleep 30"


def test_conversation_window_records_the_id_and_no_command_line(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1", tmux=FakeTmux()
    )

    entries = _recorded_entries(ws_dir)
    assert entries[0].conversation_id is not None
    assert entries[0].command_line is None


# ---------------------------------------------------------------------------
# 6. No remote-control, no visible-name flag
# ---------------------------------------------------------------------------


def test_composed_argv_carries_neither_remote_control_nor_name_flag(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    tmux = FakeTmux()
    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1", tmux=tmux
    )

    command = tmux.calls[0]["command"]
    assert "--remote-control" not in command
    assert "--name" not in command


# ---------------------------------------------------------------------------
# 7. Scrub inside the command; tmux request itself carries no scrub
# ---------------------------------------------------------------------------


def test_scrub_rides_inside_the_command_and_the_tmux_request_carries_none(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    tmux = FakeTmux()
    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1", tmux=tmux
    )

    call = tmux.calls[0]
    command = call["command"]
    # Half one: the scrub is INSIDE the command the window runs.
    assert command[0] == "env"
    for var in ("SCRUB_ONE", "SCRUB_TWO"):
        idx = command.index(var)
        assert command[idx - 1] == "-u"

    # Half two: the tmux request that creates the window carries no scrub —
    # no separate env channel was used to state it a second time.
    assert call["env"] is None


# ---------------------------------------------------------------------------
# 8. Written before success is reported, exactly once
# ---------------------------------------------------------------------------


def test_record_written_exactly_once_and_before_success_is_reported(
    tmp_path, monkeypatch
):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    calls = []
    real_append = wc.append_window_entry

    def spy(ws_dir_arg, entry):
        calls.append(entry)
        real_append(ws_dir_arg, entry)

    monkeypatch.setattr(wc, "append_window_entry", spy)

    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1", tmux=FakeTmux()
    )

    assert len(calls) == 1


def test_a_record_write_failure_propagates_rather_than_reporting_success(
    tmp_path, monkeypatch
):
    """If the write itself fails, no success can have been reported for a
    window whose record never landed — the write must happen ON THE PATH to
    a returned result, not after it."""
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    def boom(ws_dir_arg, entry):
        raise RuntimeError("disk full")

    monkeypatch.setattr(wc, "append_window_entry", boom)

    with pytest.raises(RuntimeError, match="disk full"):
        wc.compose_window(
            GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1", tmux=FakeTmux()
        )
