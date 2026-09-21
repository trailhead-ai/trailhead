"""Tests for launch/stop_workspace.py — the stop outcome type and the
preview classification (the pure half; the engine that constructs these
outcomes is a later task).

Test contract (see
task/the-stop-outcome-and-the-preview-classification):

1. Each `kind` is produced from exactly one input shape, and flipping one
   field (foreground command, presence of a conversation id) flips the
   kind.
2. A window with no record entry and a foreground process is `foreground`;
   with no entry at a shell it is `idle` — the record is not required to
   classify.
3. The shell-name set is the input that decides shell-versus-not: with a
   `shell_names` of `{"fakeshell"}`, a conversation window running
   `fakeshell` is `exited-conversation` and one running `bash` is
   `live-conversation`; with the module's default set, a conversation
   window running `2.1.278` is `live-conversation`, one running `claude` is
   `live-conversation`, and one running `bash` is `exited-conversation`.
4. `render_human` for two rows produces the count line and two indented
   rows; a name carrying a newline, and one carrying an ANSI escape
   sequence, are each rendered printable.
5. `render_human` for `StillPresent` names the session and the manual next
   step.
6. `render_json` round-trips through `json.loads` and carries every field
   named above; `exit_status` maps each type as stated.

Mirrors test_window_reconcile.py's convention: sys.path is set up for the
plugin package before any `camp.*` import, and every `camp.*` symbol is
imported inside the function that uses it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _window(window_id="@1", current_path="/ws", current_command="bash", name="win"):
    from camp.launch.tmux import TmuxWindow

    return TmuxWindow(
        window_id=window_id,
        current_path=current_path,
        current_command=current_command,
        name=name,
    )


def _entry(window_id="@1", name="win", cwd=".", conversation_id="conv-1", command_line=None):
    from camp.group.window_record import WindowEntry

    if command_line is not None:
        return WindowEntry(window_id=window_id, name=name, cwd=cwd, command_line=command_line)
    return WindowEntry(window_id=window_id, name=name, cwd=cwd, conversation_id=conversation_id)


# --- Contract item 1: kind is produced from exactly one input shape --------


def test_flipping_foreground_command_flips_conversation_kind():
    from camp.launch.stop_workspace import classify

    entries = [_entry(conversation_id="conv-1")]
    at_shell = classify([_window(current_command="bash")], entries, {"bash"})
    at_process = classify([_window(current_command="python")], entries, {"bash"})

    assert at_shell.windows[0].kind == "exited-conversation"
    assert at_process.windows[0].kind == "live-conversation"


def test_flipping_conversation_id_presence_flips_kind_at_same_command():
    from camp.launch.stop_workspace import classify

    with_entry = classify([_window(current_command="python")], [_entry(conversation_id="conv-1")], {"bash"})
    without_entry = classify([_window(current_command="python")], [], {"bash"})

    assert with_entry.windows[0].kind == "live-conversation"
    assert without_entry.windows[0].kind == "foreground"


# --- Contract item 2: no record entry ---------------------------------------


def test_no_entry_at_foreground_process_is_foreground():
    from camp.launch.stop_workspace import classify

    result = classify([_window(current_command="pytest")], [], {"bash"})

    assert result.windows[0].kind == "foreground"
    assert result.windows[0].conversation_id is None


def test_no_entry_at_a_shell_is_idle():
    from camp.launch.stop_workspace import classify

    result = classify([_window(current_command="bash")], [], {"bash"})

    assert result.windows[0].kind == "idle"
    assert result.windows[0].conversation_id is None


# --- Contract item 3: shell_names decides shell-versus-not ------------------


def test_custom_shell_names_set_decides_classification():
    from camp.launch.stop_workspace import classify

    entries = [_entry(conversation_id="conv-1")]

    exited = classify([_window(current_command="fakeshell")], entries, {"fakeshell"})
    live = classify([_window(current_command="bash")], entries, {"fakeshell"})

    assert exited.windows[0].kind == "exited-conversation"
    assert live.windows[0].kind == "live-conversation"


def test_default_shell_names_treats_version_string_and_claude_as_live():
    from camp.launch.stop_workspace import DEFAULT_SHELL_NAMES, classify

    entries = [_entry(conversation_id="conv-1")]

    version_string = classify([_window(current_command="2.1.278")], entries, DEFAULT_SHELL_NAMES)
    plain_claude = classify([_window(current_command="claude")], entries, DEFAULT_SHELL_NAMES)
    at_shell = classify([_window(current_command="bash")], entries, DEFAULT_SHELL_NAMES)

    assert version_string.windows[0].kind == "live-conversation"
    assert plain_claude.windows[0].kind == "live-conversation"
    assert at_shell.windows[0].kind == "exited-conversation"


def test_default_shell_names_includes_the_basename_of_dollar_shell():
    from camp.launch.stop_workspace import default_shell_names

    names = default_shell_names({"SHELL": "/usr/local/bin/fish"})

    assert "fish" in names
    assert "bash" in names  # the common set is always present


# --- Contract item 4: render_human rows and escaping ------------------------


def test_render_human_two_rows_produces_count_line_and_two_rows():
    from camp.launch.stop_workspace import PreviewRow, Stopped, StopPreview, render_human

    preview = StopPreview(
        windows=(
            PreviewRow(
                window_id="@1",
                name="planning",
                conversation_id="8f2c",
                kind="live-conversation",
                command="2.1.278",
            ),
            PreviewRow(
                window_id="@3",
                name="review",
                conversation_id="41aa",
                kind="exited-conversation",
                command="zsh",
            ),
        )
    )
    outcome = Stopped(slug="camp-cli", group="trailhead", tmux_session="camp-trailhead-camp-cli", preview=preview)

    rendered = render_human(outcome)
    lines = rendered.split("\n")

    assert lines[0] == "stopping camp-trailhead-camp-cli: 2 windows"
    assert "@1" in lines[1] and "planning" in lines[1] and "8f2c" in lines[1] and "live" in lines[1]
    assert "@3" in lines[2] and "review" in lines[2] and "41aa" in lines[2] and "exited" in lines[2]
    assert lines[3] == "stopped camp-trailhead-camp-cli"


def test_render_human_escapes_a_newline_in_a_window_name():
    from camp.launch.stop_workspace import PreviewRow, Stopped, StopPreview, render_human

    preview = StopPreview(
        windows=(
            PreviewRow(
                window_id="@1",
                name="evil\nInjected: pwned",
                conversation_id=None,
                kind="foreground",
                command="pytest",
            ),
        )
    )
    outcome = Stopped(slug="s", group="g", tmux_session="camp-trailhead-s", preview=preview)

    rendered = render_human(outcome)

    assert (
        "\n" not in rendered.split("\nstopped")[0].replace("stopping camp-trailhead-s: 1 windows", "", 1)
        or "\\x0a" in rendered
    )
    # the header, the row, and the outcome line are the only real newlines
    assert rendered.count("\n") == 2
    assert "\\x0a" in rendered


def test_render_human_escapes_an_ansi_escape_sequence_in_a_window_name():
    from camp.launch.stop_workspace import PreviewRow, Stopped, StopPreview, render_human

    preview = StopPreview(
        windows=(
            PreviewRow(
                window_id="@1",
                name="evil\x1b[31mred",
                conversation_id=None,
                kind="foreground",
                command="pytest",
            ),
        )
    )
    outcome = Stopped(slug="s", group="g", tmux_session="camp-trailhead-s", preview=preview)

    rendered = render_human(outcome)

    assert "\x1b" not in rendered
    assert "\\x1b" in rendered


# --- Contract item 5: render_human for StillPresent -------------------------


def test_render_human_for_still_present_names_session_and_next_step():
    from camp.launch.stop_workspace import PreviewRow, StillPresent, StopPreview, render_human

    preview = StopPreview(
        windows=(
            PreviewRow(
                window_id="@1",
                name="planning",
                conversation_id="8f2c",
                kind="live-conversation",
                command="2.1.278",
            ),
        )
    )
    outcome = StillPresent(slug="camp-cli", group="trailhead", tmux_session="camp-trailhead-camp-cli", preview=preview)

    rendered = render_human(outcome)

    assert "camp-trailhead-camp-cli" in rendered
    assert "run camp stop again" in rendered
    assert "tmux kill-session -t =camp-trailhead-camp-cli" in rendered


# --- Contract item 6: render_json round-trip and exit_status table ----------


def test_render_json_round_trips_and_carries_every_field():
    from camp.launch.stop_workspace import PreviewRow, Stopped, StopPreview, render_json

    preview = StopPreview(
        windows=(
            PreviewRow(
                window_id="@1",
                name="planning",
                conversation_id="8f2c",
                kind="live-conversation",
                command="2.1.278",
            ),
            PreviewRow(
                window_id="@3",
                name="review",
                conversation_id="41aa",
                kind="exited-conversation",
                command="zsh",
            ),
            PreviewRow(window_id="@5", name="build", conversation_id=None, kind="foreground", command="pytest"),
            PreviewRow(window_id="@7", name="idle-one", conversation_id=None, kind="idle", command="zsh"),
        ),
        reconciled=True,
    )
    outcome = Stopped(slug="camp-cli", group="trailhead", tmux_session="camp-trailhead-camp-cli", preview=preview)

    obj = render_json(outcome)
    round_tripped = json.loads(json.dumps(obj))

    assert round_tripped == obj
    assert round_tripped["windows"] == 4
    assert round_tripped["live_conversations"] == ["8f2c"]
    assert round_tripped["exited_conversations"] == ["41aa"]
    assert round_tripped["foreground"] == ["build"]
    assert round_tripped["reconciled"] is True
    assert round_tripped["outcome"] == "stopped"


def test_exit_status_covers_every_member_via_reflection():
    from camp.launch import stop_workspace as sw

    subclasses = sw.StopOutcome.__subclasses__()
    assert set(subclasses) == {
        sw.Stopped,
        sw.NotRunning,
        sw.StillPresent,
        sw.RefusedNoWorkspace,
        sw.RefusedTmuxUnanswered,
    }

    expected = {
        sw.Stopped: 0,
        sw.NotRunning: 0,
        sw.StillPresent: 1,
        sw.RefusedNoWorkspace: 1,
        sw.RefusedTmuxUnanswered: 1,
    }
    for cls, status in expected.items():
        instance = cls(slug="s", group="g", tmux_session="t", preview=sw.StopPreview())
        assert sw.exit_status(instance) == status


def test_foreground_row_names_the_process_it_would_lose():
    from camp.launch.stop_workspace import PreviewRow, Stopped, StopPreview, render_human

    def line_for(command):
        preview = StopPreview(
            windows=(
                PreviewRow(
                    window_id="@5",
                    name="build",
                    conversation_id=None,
                    kind="foreground",
                    command=command,
                ),
            )
        )
        outcome = Stopped(slug="camp-cli", group="trailhead", tmux_session="camp-trailhead-camp-cli", preview=preview)
        return render_human(outcome).splitlines()[1]

    assert line_for("pytest") == '  @5 "build"  foreground: pytest'
    assert line_for("vim") == '  @5 "build"  foreground: vim'


def test_classify_carries_each_window_s_current_command_onto_its_row():
    from camp.launch.stop_workspace import DEFAULT_SHELL_NAMES, classify
    from camp.launch.tmux import TmuxWindow

    windows = [
        TmuxWindow(window_id="@5", current_path="/w", current_command="pytest", name="build"),
        TmuxWindow(window_id="@7", current_path="/w", current_command="zsh", name="idle-one"),
    ]
    result = classify(windows, [], DEFAULT_SHELL_NAMES)
    assert [(r.kind, r.command) for r in result.windows] == [("foreground", "pytest"), ("idle", "zsh")]
