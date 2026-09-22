"""Tests for camp.launch.resurrect — the pure planner behind resurrection.

Test contract (see
task/the-resurrection-plan-is-pure-restore-with-a-stub-or-drop-with-a-reason):

1. An entry under the root with an existing directory is `Restore` with that
   resolved directory; the same entry with `cwd` pointing through a symlink
   outside the root is `Drop` naming the recorded path.
2. An entry whose resolved directory is under a declared credential store is
   `Drop` whose reason does not contain the path; the same entry with no such
   declaration is `Restore`.
3. An entry whose directory does not exist is `Drop` naming it as gone; order
   of the returned decisions equals record order across a mixed record.
4. A conversation entry's stub lines vary with transcript/harness presence
   (three inputs, three line sets).
5. A command-line entry's stub lines are exactly the one opened-with line,
   and the argv carries the command line as an argument, not inside the
   script.
6. The `-u` list in the script matches the fake harness's
   `session_launch_env_unset()`; empty harness gives no `-u`.
7. `render_plan_lines` on a `Drop` with a conversation id includes it; a line
   containing a control character in the window name renders escaped.
8. A command line carrying an ESC byte and an OSC sequence, and a
   conversation id carrying a C1 byte, each reach the stub argv escaped.

Every test drives `plan_resurrection` / `render_plan_lines` directly against
real `WindowEntry` records and a fake harness — never a real tmux or
filesystem beyond `tmp_path`.
"""

from __future__ import annotations

import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.group.window_record import WindowEntry  # noqa: E402
from camp.launch.resurrect import (  # noqa: E402
    STUB_SCRIPT,
    Drop,
    Restore,
    plan_resurrection,
    render_plan_lines,
)


class FakeHarness:
    """Stand-in for the trailhead harness seam — only the three methods
    this planner reads."""

    def __init__(self, *, scrub=(), transcript=None, resume=None):
        self._scrub = tuple(scrub)
        self._transcript = transcript
        self._resume = resume

    def session_launch_env_unset(self):
        return list(self._scrub)

    def session_transcript_path(self, session_id, workspace, *, env=None):
        return self._transcript

    def session_resume(self, session_id):
        return self._resume


def _conv_entry(**overrides):
    fields = dict(
        window_id="@1",
        name="work",
        cwd="member",
        conversation_id="8f2c1a3e-aaaa-bbbb-cccc-111122223333",
        command_line=None,
    )
    fields.update(overrides)
    return WindowEntry(**fields)


def _cmd_entry(**overrides):
    fields = dict(
        window_id="@2",
        name="tests",
        cwd="member",
        conversation_id=None,
        command_line="pytest -x tests/",
    )
    fields.update(overrides)
    return WindowEntry(**fields)


def _mkws(tmp_path) -> Path:
    ws = tmp_path / "ws"
    (ws / "member").mkdir(parents=True)
    return ws


# -- 1. containment: resolved directory vs. symlink escape ------------------


def test_entry_under_root_is_restore_with_resolved_directory(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None)
    assert isinstance(decision, Restore)
    assert decision.directory == (ws / "member").resolve()


def test_entry_through_symlink_outside_root_is_drop_naming_recorded_path(tmp_path):
    ws = _mkws(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = ws / "escape"
    link.symlink_to(outside)
    entry = _conv_entry(cwd="escape")
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None)
    assert isinstance(decision, Drop)
    assert "escape" in decision.reason


# -- 2. credential store floor -----------------------------------------------


def test_entry_under_credential_store_is_drop_without_the_path(tmp_path, monkeypatch):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")

    import camp.launch.resurrect as resurrect_mod
    from camp.launch.session import LaunchError

    def _fake_assert(resolved, *, env):
        raise LaunchError("camp: cannot launch — credential store")

    monkeypatch.setattr(resurrect_mod, "assert_not_a_credential_store", _fake_assert)

    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None)
    assert isinstance(decision, Drop)
    assert str(ws / "member") not in decision.reason
    assert "member" not in decision.reason


def test_entry_not_under_credential_store_is_restore(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None)
    assert isinstance(decision, Restore)


# -- 3. missing directory, and record order preserved ------------------------


def test_entry_with_missing_directory_is_drop_naming_it_gone(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    entry = _conv_entry(cwd="gone")
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None)
    assert isinstance(decision, Drop)
    assert "gone" in decision.reason


def test_mixed_record_preserves_order(tmp_path):
    ws = _mkws(tmp_path)
    (ws / "second").mkdir()
    present = _conv_entry(window_id="@1", cwd="member")
    missing = _conv_entry(window_id="@2", cwd="gone")
    present2 = _conv_entry(window_id="@3", cwd="second")
    decisions = plan_resurrection([present, missing, present2], ws, env={"HOME": str(tmp_path)}, harness=None)
    assert [d.entry.window_id for d in decisions] == ["@1", "@2", "@3"]
    assert isinstance(decisions[0], Restore)
    assert isinstance(decisions[1], Drop)
    assert isinstance(decisions[2], Restore)


# -- 4. conversation entry stub lines: transcript / harness variants --------


def _lines_of(restore: Restore) -> list[str]:
    # argv is ["sh", "-c", script, "camp-resurrect", *lines]
    return restore.argv[4:]


def test_conversation_with_transcript_and_resume_gets_id_and_resume_line(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    harness = FakeHarness(
        transcript=Path("/some/transcript.jsonl"),
        resume=["claude", "--resume", entry.conversation_id],
    )
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=harness)
    lines = _lines_of(decision)
    assert lines == [
        f"camp: this window held conversation {entry.conversation_id}",
        f"camp: resume it with: claude --resume {entry.conversation_id}",
    ]


def test_conversation_with_no_transcript_gets_single_no_transcript_line(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    harness = FakeHarness(transcript=None, resume=["claude", "--resume", entry.conversation_id])
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=harness)
    lines = _lines_of(decision)
    assert lines == [
        f"camp: this window held conversation {entry.conversation_id}, but no "
        "transcript for it exists on this machine"
    ]


def test_conversation_with_no_harness_gets_id_and_no_harness_line(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None)
    lines = _lines_of(decision)
    assert lines == [
        f"camp: this window held conversation {entry.conversation_id}",
        "camp: no harness is configured for this group, so camp cannot compose a resume command",
    ]


# -- 5. command-line entry: single line, argv carries it as an element -----


def test_command_line_entry_gets_single_opened_with_line(tmp_path):
    ws = _mkws(tmp_path)
    entry = _cmd_entry(cwd="member", command_line="pytest -x tests/")
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None)
    lines = _lines_of(decision)
    assert lines == ["camp: this window was opened with: pytest -x tests/"]


def test_command_line_entry_script_is_stub_script_unchanged_regardless_of_line(tmp_path):
    ws = _mkws(tmp_path)
    short = _cmd_entry(cwd="member", command_line="ls")
    long = _cmd_entry(cwd="member", command_line="a very long recorded command line indeed")
    (short_decision,) = plan_resurrection([short], ws, env={"HOME": str(tmp_path)}, harness=None)
    (long_decision,) = plan_resurrection([long], ws, env={"HOME": str(tmp_path)}, harness=None)
    assert short_decision.argv[2] == STUB_SCRIPT
    assert long_decision.argv[2] == STUB_SCRIPT
    # the command line is a later argv element, never inside the script
    assert "ls" not in short_decision.argv[2]
    assert "a very long recorded command line indeed" not in long_decision.argv[2]
    assert short_decision.argv[4] == "camp: this window was opened with: ls"
    assert long_decision.argv[4] == (
        "camp: this window was opened with: a very long recorded command line indeed"
    )


# -- 6. the -u list matches the harness scrub --------------------------------


def test_script_carries_the_harness_scrub_as_env_unset_flags(tmp_path):
    ws = _mkws(tmp_path)
    entry = _cmd_entry(cwd="member")
    harness = FakeHarness(scrub=("ANTHROPIC_API_KEY", "SOME_TOKEN"))
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=harness)
    assert "-u ANTHROPIC_API_KEY" in decision.argv[2]
    assert "-u SOME_TOKEN" in decision.argv[2]


def test_empty_harness_scrub_gives_no_u_flags(tmp_path):
    ws = _mkws(tmp_path)
    entry = _cmd_entry(cwd="member")
    harness = FakeHarness(scrub=())
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=harness)
    assert "-u" not in decision.argv[2]
    assert decision.argv[2] == STUB_SCRIPT


# -- 7. render_plan_lines: conversation id included, control chars escaped --


def test_render_plan_lines_includes_conversation_id_for_a_drop():
    entry = _conv_entry(window_id="@3", name="review", cwd="gone")
    decision = Drop(entry, "directory gone no longer exists")
    (line,) = render_plan_lines([decision])
    assert entry.conversation_id in line


def test_render_plan_lines_escapes_control_character_in_window_name():
    entry = _conv_entry(window_id="@3", name="rev\x07iew", cwd="gone", conversation_id=None, command_line="x")
    decision = Drop(entry, "directory gone no longer exists")
    (line,) = render_plan_lines([decision])
    assert "\x07" not in line
    assert "\\x07" in line


# -- 8. control/OSC/C1 bytes reach the stub argv escaped ---------------------


def test_command_line_with_esc_and_osc_reaches_argv_escaped(tmp_path):
    ws = _mkws(tmp_path)
    raw = "before\x1b]0;evil\x07after"
    entry = _cmd_entry(cwd="member", command_line=raw)
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None)
    line = decision.argv[4]
    assert "\x1b" not in line
    assert "\x07" not in line
    assert "\\x1b" in line
    assert "\\x07" in line


def test_command_line_without_control_bytes_reaches_argv_unchanged(tmp_path):
    ws = _mkws(tmp_path)
    entry = _cmd_entry(cwd="member", command_line="pytest -x tests/")
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None)
    assert decision.argv[4] == "camp: this window was opened with: pytest -x tests/"


def test_conversation_id_with_c1_byte_reaches_argv_escaped(tmp_path):
    ws = _mkws(tmp_path)
    raw_id = "conv-\x9bid"
    entry = _conv_entry(cwd="member", conversation_id=raw_id)
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None)
    id_line = decision.argv[4]
    assert "\x9b" not in id_line
    assert "\\x9b" in id_line


def test_conversation_id_without_c1_byte_reaches_argv_unchanged(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member", conversation_id="plain-conv-id")
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None)
    id_line = decision.argv[4]
    assert id_line == "camp: this window held conversation plain-conv-id"
