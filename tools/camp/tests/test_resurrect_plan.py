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

import os
import subprocess
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.group.window_record import WindowEntry  # noqa: E402
from camp.launch.resurrect import (  # noqa: E402
    STUB_SCRIPT,
    Drop,
    Restore,
    _NO_HARNESS_LINE,
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
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None, group=None)
    assert isinstance(decision, Restore)
    assert decision.directory == (ws / "member").resolve()


def test_entry_through_symlink_outside_root_is_drop_naming_recorded_path(tmp_path):
    ws = _mkws(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = ws / "escape"
    link.symlink_to(outside)
    entry = _conv_entry(cwd="escape")
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None, group=None)
    assert isinstance(decision, Drop)
    assert "escape" in decision.reason


# -- 2. credential store floor -----------------------------------------------


def _install_account(tmp_path, account_path):
    """Write a group config declaring `[launch] account = account_path`, and
    return the env that points camp's config resolver at it, so AC41's re-check is
    pinned against a REAL declared credential store, not a monkeypatched
    `assert_not_a_credential_store`."""
    groups_dir = tmp_path / "camp-config" / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    body = (
        '[group]\nname = "testgroup"\n\n'
        '[[members]]\nname = "myrepo"\nrepo_root = "/tmp/myrepo"\n\n'
        f'[launch]\naccount = "{account_path}"\n'
    )
    (groups_dir / "testgroup.toml").write_text(body, encoding="utf-8")
    return {"HOME": str(tmp_path), "CAMP_CONFIG_DIR": str(tmp_path / "camp-config")}


def test_entry_under_credential_store_is_drop_without_the_path(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    env = _install_account(tmp_path, str(ws / "member"))

    (decision,) = plan_resurrection([entry], ws, env=env, harness=None, group=None)
    assert isinstance(decision, Drop)
    assert str(ws / "member") not in decision.reason
    assert "member" not in decision.reason


def test_entry_not_under_credential_store_is_restore(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None, group=None)
    assert isinstance(decision, Restore)


# -- 3. missing directory, and record order preserved ------------------------


def test_entry_with_missing_directory_is_drop_naming_it_gone(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    entry = _conv_entry(cwd="gone")
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None, group=None)
    assert isinstance(decision, Drop)
    assert "gone" in decision.reason


def test_mixed_record_preserves_order(tmp_path):
    ws = _mkws(tmp_path)
    (ws / "second").mkdir()
    present = _conv_entry(window_id="@1", cwd="member")
    missing = _conv_entry(window_id="@2", cwd="gone")
    present2 = _conv_entry(window_id="@3", cwd="second")
    decisions = plan_resurrection(
        [present, missing, present2], ws, env={"HOME": str(tmp_path)}, harness=None, group=None
    )
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
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=None)
    lines = _lines_of(decision)
    assert lines == [
        f"camp: this window held conversation {entry.conversation_id}",
        f"camp: resume it with: claude --resume {entry.conversation_id}",
    ]


def test_conversation_with_no_transcript_gets_single_no_transcript_line(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    harness = FakeHarness(transcript=None, resume=["claude", "--resume", entry.conversation_id])
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=None)
    lines = _lines_of(decision)
    assert lines == [
        f"camp: this window held conversation {entry.conversation_id}, but no "
        "transcript for it exists on this machine"
    ]


def test_conversation_with_no_harness_gets_id_and_no_harness_line(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None, group=None)
    lines = _lines_of(decision)
    assert lines == [
        f"camp: this window held conversation {entry.conversation_id}",
        "camp: no harness is configured for this group, so camp cannot compose a resume command",
    ]


# -- 5. command-line entry: single line, argv carries it as an element -----


def test_command_line_entry_gets_single_opened_with_line(tmp_path):
    ws = _mkws(tmp_path)
    entry = _cmd_entry(cwd="member", command_line="pytest -x tests/")
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None, group=None)
    lines = _lines_of(decision)
    assert lines == ["camp: this window was opened with: pytest -x tests/"]


def test_command_line_entry_script_is_stub_script_unchanged_regardless_of_line(tmp_path):
    ws = _mkws(tmp_path)
    short = _cmd_entry(cwd="member", command_line="ls")
    long = _cmd_entry(cwd="member", command_line="a very long recorded command line indeed")
    (short_decision,) = plan_resurrection([short], ws, env={"HOME": str(tmp_path)}, harness=None, group=None)
    (long_decision,) = plan_resurrection([long], ws, env={"HOME": str(tmp_path)}, harness=None, group=None)
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
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=None)
    assert "-u ANTHROPIC_API_KEY" in decision.argv[2]
    assert "-u SOME_TOKEN" in decision.argv[2]


def test_a_scrub_name_outside_the_shell_identifier_shape_is_refused(tmp_path):
    """The harness's `session_launch_env_unset()` names are spliced into the
    stub script by string replace — a harness-contract violation (not vault
    input), so a name outside a plain shell identifier's shape must raise
    rather than silently reach the composed shell source. Varied across a
    value carrying shell metacharacters and one carrying whitespace, so the
    assertion pins the general shape check rather than one blocked literal."""
    ws = _mkws(tmp_path)
    entry = _cmd_entry(cwd="member")

    from camp.launch.session import LaunchError

    injected = FakeHarness(scrub=("SAFE_NAME", "FOO; rm -rf /"))
    with pytest.raises(LaunchError):
        plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=injected, group=None)

    spaced = FakeHarness(scrub=("FOO BAR",))
    with pytest.raises(LaunchError):
        plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=spaced, group=None)


def test_empty_harness_scrub_gives_no_u_flags(tmp_path):
    ws = _mkws(tmp_path)
    entry = _cmd_entry(cwd="member")
    harness = FakeHarness(scrub=())
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=None)
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
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None, group=None)
    line = decision.argv[4]
    assert "\x1b" not in line
    assert "\x07" not in line
    assert "\\x1b" in line
    assert "\\x07" in line


def test_command_line_without_control_bytes_reaches_argv_unchanged(tmp_path):
    ws = _mkws(tmp_path)
    entry = _cmd_entry(cwd="member", command_line="pytest -x tests/")
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None, group=None)
    assert decision.argv[4] == "camp: this window was opened with: pytest -x tests/"


def test_conversation_id_with_c1_byte_reaches_argv_escaped(tmp_path):
    ws = _mkws(tmp_path)
    raw_id = "conv-\x9bid"
    entry = _conv_entry(cwd="member", conversation_id=raw_id)
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None, group=None)
    id_line = decision.argv[4]
    assert "\x9b" not in id_line
    assert "\\x9b" in id_line


def test_conversation_id_without_c1_byte_reaches_argv_unchanged(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member", conversation_id="plain-conv-id")
    (decision,) = plan_resurrection([entry], ws, env={"HOME": str(tmp_path)}, harness=None, group=None)
    id_line = decision.argv[4]
    assert id_line == "camp: this window held conversation plain-conv-id"


# ---------------------------------------------------------------------------
# 9. Account binding — resurrection resolves the same binding the session does
#
# Resurrection binds the SAME account the workspace session itself carries,
# through the SAME `resolve_launch_environment` resolver, so a group with a
# declared account resurrects a transcript stored under THAT account, and its
# stub's `exec env` carries the binding — never a second, independent read of
# `[launch] account`.
# ---------------------------------------------------------------------------

GROUP_NO_ACCOUNT = {"group": {"name": "testgroup"}}
GROUP_WITH_ACCOUNT = {
    "group": {"name": "testgroup"},
    "launch": {"account": "/tmp/acct-a"},
}
GROUP_WITH_REFUSED_ACCOUNT = {
    "group": {"name": "testgroup"},
    "launch": {"account": "claude-levr"},
}


class _AccountHarness(FakeHarness):
    """A `FakeHarness` that also answers `session_launch_env_set` — the
    method `resolve_launch_environment` calls to bind a declared account —
    and a `session_transcript_path` that only finds the transcript when the
    ENV it is called with already carries the bound account, so a test can
    tell whether resurrection looked it up under the raw environment or the
    one `resolve_launch_environment` resolves."""

    _ACCOUNT_VAR = "CLAUDE_CONFIG_DIR"
    name = "accountharness"

    def __init__(self, *, transcript_by_account=None, resume=None, refuse_account=False, **kwargs):
        super().__init__(**kwargs)
        self._transcript_by_account = dict(transcript_by_account or {})
        self._resume = resume
        self._refuse_account = refuse_account

    def session_launch_env_set(self, account, *, env=None):
        from trailhead.harness import HarnessError

        if self._refuse_account and account is not None:
            raise HarnessError(f"account {account!r} must be an absolute path")
        if account is None:
            return {}
        return {self._ACCOUNT_VAR: account}

    def session_transcript_path(self, session_id, workspace, *, env=None):
        bound = (env or {}).get(self._ACCOUNT_VAR)
        return self._transcript_by_account.get(bound)

    def session_resume(self, session_id):
        return self._resume


def _lines_after_bindings(restore: Restore, n_bindings: int) -> list[str]:
    """Like `_lines_of`, but for a decision whose argv also carries
    *n_bindings* binding VALUES ahead of the stub lines (see
    `plan_resurrection`'s own argv shape: `["sh", "-c", script,
    "camp-resurrect", *binding_values, *escaped_lines]`)."""
    return restore.argv[4 + n_bindings :]


def test_declared_account_resurrects_with_the_resume_line_when_transcript_is_under_that_account(
    tmp_path,
):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    transcript = Path("/some/transcript.jsonl")
    harness = _AccountHarness(
        transcript_by_account={"/tmp/acct-a": transcript},
        resume=["claude", "--resume", entry.conversation_id],
    )
    (decision,) = plan_resurrection(
        [entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=GROUP_WITH_ACCOUNT
    )
    lines = _lines_after_bindings(decision, 1)
    assert lines == [
        f"camp: this window held conversation {entry.conversation_id}",
        f"camp: resume it with: claude --resume {entry.conversation_id}",
    ]


def test_no_declared_account_never_finds_a_transcript_stored_under_an_account(tmp_path):
    """The control case for the test above: the SAME harness and transcript
    store, but no declared account — the transcript is stored under
    `/tmp/acct-a`, which nothing binds, so it must not be found."""
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    transcript = Path("/some/transcript.jsonl")
    harness = _AccountHarness(transcript_by_account={"/tmp/acct-a": transcript})
    (decision,) = plan_resurrection(
        [entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=GROUP_NO_ACCOUNT
    )
    lines = _lines_of(decision)
    assert lines == [
        f"camp: this window held conversation {entry.conversation_id}, but no "
        "transcript for it exists on this machine"
    ]


def test_declared_account_stub_argv_carries_the_binding(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    harness = _AccountHarness(scrub=("SOME_TOKEN",))
    (decision,) = plan_resurrection(
        [entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=GROUP_WITH_ACCOUNT
    )
    assert "/tmp/acct-a" in decision.argv
    assert "-u SOME_TOKEN" in decision.argv[2]
    assert "CLAUDE_CONFIG_DIR=" in decision.argv[2]


def test_no_account_stub_argv_carries_no_binding(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    harness = _AccountHarness(scrub=("SOME_TOKEN",))
    (decision,) = plan_resurrection(
        [entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=GROUP_NO_ACCOUNT
    )
    assert "CLAUDE_CONFIG_DIR=" not in decision.argv[2]
    assert "/tmp/acct-a" not in decision.argv


def test_a_refused_account_gives_the_refusal_line_and_no_resume_line(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    harness = _AccountHarness(
        transcript_by_account={}, resume=["claude", "--resume", entry.conversation_id],
        refuse_account=True,
    )
    (decision,) = plan_resurrection(
        [entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=GROUP_WITH_REFUSED_ACCOUNT
    )
    lines = _lines_of(decision)
    assert lines[0] == f"camp: this window held conversation {entry.conversation_id}"
    assert not any(line.startswith("camp: resume it with:") for line in lines)
    assert "claude-levr" in lines[1]


def test_a_refused_account_stub_still_scrubs_but_carries_no_binding(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    harness = _AccountHarness(scrub=("SOME_TOKEN",), refuse_account=True)
    (decision,) = plan_resurrection(
        [entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=GROUP_WITH_REFUSED_ACCOUNT
    )
    assert "-u SOME_TOKEN" in decision.argv[2]
    assert "CLAUDE_CONFIG_DIR=" not in decision.argv[2]
    assert "claude-levr" not in decision.argv[2], "the value never touches the script text itself"


# ---------------------------------------------------------------------------
# 10. The stub argv actually runs under a real `sh` — proves the generated
# script behaves as documented (values arrive verbatim, nothing is
# re-parsed as shell source, the scrub actually removes what it names) and
# catches the `"$10"` positional bug (ten or more bindings shift wrong once
# a two-digit position is unbraced) that inspecting the script's source
# text cannot.
# ---------------------------------------------------------------------------


class _StubHarness(FakeHarness):
    """A `FakeHarness` whose `session_launch_env_set` answers a FIXED
    binding mapping regardless of the declared account — lets a test choose
    exactly how many binding names/values the generated stub script must
    capture, independent of any particular account string."""

    name = "stubharness"

    def __init__(self, *, bindings=None, **kwargs):
        super().__init__(**kwargs)
        self._bindings = dict(bindings or {})

    def session_launch_env_set(self, account, *, env=None):
        return dict(self._bindings)


_STUB_GROUP = {"group": {"name": "testgroup"}, "launch": {"account": "/tmp/acct"}}


def _run_stub(decision: Restore, *, extra_env: dict) -> subprocess.CompletedProcess:
    """Run a `Restore` decision's argv under a real `sh`, with `$SHELL` set
    to `/usr/bin/env` so the stub's final `exec ... "${SHELL:-sh}"` runs
    `env` itself — printing the exact environment the stub built, one
    `NAME=value` line per variable. Lets a test assert on the environment a
    resurrected pane would actually run under, rather than on the generated
    script's source text."""
    env = dict(os.environ)
    env["SHELL"] = "/usr/bin/env"
    env.update(extra_env)
    return subprocess.run(
        decision.argv, env=env, capture_output=True, text=True, timeout=10
    )


def _env_lines_of(stdout: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in stdout.splitlines():
        if "=" in line:
            name, _, value = line.partition("=")
            parsed[name] = value
    return parsed


def test_stub_delivers_a_binding_value_with_shell_metacharacters_verbatim(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    marker = tmp_path / "pwned"
    weird_value = f"""-weird $(touch {marker}) `id` ' " ;"""
    harness = _StubHarness(bindings={"CAMP_TEST_VAR": weird_value})
    (decision,) = plan_resurrection(
        [entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=_STUB_GROUP
    )
    result = _run_stub(decision, extra_env={})
    assert not marker.exists(), (
        "a shell metacharacter embedded in a binding value must never be "
        "re-parsed as shell source"
    )
    env_out = _env_lines_of(result.stdout)
    assert env_out["CAMP_TEST_VAR"] == weird_value


def test_stub_delivers_twelve_bindings_each_with_their_own_value(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    # Letter-keyed, not digit-keyed: a naive `"$10"` (unbraced) misparses as
    # `"${1}0"` — string-concatenating $1's value with a literal "0" — and a
    # digit-suffixed value like "value-10" would coincidentally still equal
    # "value-1" + "0", passing even with the bug. Letters rule that out.
    letters = "abcdefghijkl"
    bindings = {f"CAMP_VAR_{i}": f"binding-{letters[i - 1]}" for i in range(1, 13)}
    harness = _StubHarness(bindings=bindings)
    (decision,) = plan_resurrection(
        [entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=_STUB_GROUP
    )
    result = _run_stub(decision, extra_env={})
    env_out = _env_lines_of(result.stdout)
    for name, value in bindings.items():
        assert env_out.get(name) == value, (
            f"{name} arrived as {env_out.get(name)!r}, expected {value!r} — "
            "a later positional (>=10) shifted onto an earlier one"
        )


def test_stub_zero_bindings_still_runs_and_scrubs(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    harness = _StubHarness(bindings={}, scrub=("CAMP_SCRUB_ZERO",))
    (decision,) = plan_resurrection(
        [entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=None
    )
    result = _run_stub(decision, extra_env={"CAMP_SCRUB_ZERO": "should-not-survive"})
    env_out = _env_lines_of(result.stdout)
    assert "CAMP_SCRUB_ZERO" not in env_out


def test_stub_one_binding_arrives_correctly(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    harness = _StubHarness(bindings={"CAMP_SOLO": "solo-value"})
    (decision,) = plan_resurrection(
        [entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=_STUB_GROUP
    )
    result = _run_stub(decision, extra_env={})
    env_out = _env_lines_of(result.stdout)
    assert env_out.get("CAMP_SOLO") == "solo-value"


@pytest.mark.parametrize("flag_line", ["-n", "-e", "--"])
def test_stub_lines_starting_with_a_dash_flag_print_verbatim(tmp_path, flag_line):
    ws = _mkws(tmp_path)
    entry = _cmd_entry(cwd="member", command_line=flag_line)
    harness = _StubHarness(bindings={})
    (decision,) = plan_resurrection(
        [entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=None
    )
    result = _run_stub(decision, extra_env={})
    expected = f"camp: this window was opened with: {flag_line}"
    assert expected in result.stdout.splitlines()


def test_stub_scrub_removes_the_named_variable(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    harness = _StubHarness(bindings={}, scrub=("CAMP_SCRUB_ME",))
    (decision,) = plan_resurrection(
        [entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=None
    )
    result = _run_stub(decision, extra_env={"CAMP_SCRUB_ME": "should-not-survive"})
    env_out = _env_lines_of(result.stdout)
    assert "CAMP_SCRUB_ME" not in env_out


def test_stub_scrub_leaves_other_variables_alone(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    harness = _StubHarness(bindings={}, scrub=("CAMP_SCRUB_ME",))
    (decision,) = plan_resurrection(
        [entry], ws, env={"HOME": str(tmp_path)}, harness=harness, group=None
    )
    result = _run_stub(
        decision,
        extra_env={"CAMP_SCRUB_ME": "should-not-survive", "CAMP_KEEP_ME": "still-here"},
    )
    env_out = _env_lines_of(result.stdout)
    assert env_out.get("CAMP_KEEP_ME") == "still-here"

# ---------------------------------------------------------------------------
# 11. `env=None` means "the process environment" all the way through
# `_resolve_binding` — a caller that forwards `None` (never `{}`) is the
# resurrection engine's own contract with `resolve_launch_environment`.
#
# Driven directly against `_resolve_binding` (never through `plan_resurrection`,
# whose own credential-store check calls `Path.home()` for a falsy `env` and
# would trip this suite's real-home guard regardless of this fix) so the test
# isolates exactly the environment-forwarding contract in question.
# ---------------------------------------------------------------------------


def test_resolve_binding_env_none_uses_the_process_environment(monkeypatch):
    from camp.launch.resurrect import _resolve_binding

    monkeypatch.setenv("CAMP_ENV_MARKER", "present")
    harness = _AccountHarness()

    binding, refusal, launch_env = _resolve_binding(harness, GROUP_NO_ACCOUNT, None)

    assert refusal is None
    assert launch_env.get("CAMP_ENV_MARKER") == "present"


def test_resolve_binding_env_empty_dict_never_reaches_the_process_environment(monkeypatch):
    from camp.launch.resurrect import _resolve_binding

    monkeypatch.setenv("CAMP_ENV_MARKER", "present")
    harness = _AccountHarness()

    binding, refusal, launch_env = _resolve_binding(harness, GROUP_NO_ACCOUNT, {})

    assert refusal is None
    assert launch_env.get("CAMP_ENV_MARKER") is None


# ---------------------------------------------------------------------------
# 12. A declared account with no recognized harness fails closed exactly
# like a harness-refused account: a refusal line, never a resume line, and
# never a silently unbound/unscrubbed stub.
# ---------------------------------------------------------------------------


def test_declared_account_with_no_harness_refuses_with_no_resume_line(tmp_path):
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    (decision,) = plan_resurrection(
        [entry], ws, env={"HOME": str(tmp_path)}, harness=None, group=GROUP_WITH_ACCOUNT
    )
    lines = _lines_of(decision)
    assert lines[0] == f"camp: this window held conversation {entry.conversation_id}"
    assert lines[1] != _NO_HARNESS_LINE, (
        "a declared account with no recognized harness must fail closed with "
        "its OWN refusal, distinct from the generic no-harness-at-all line"
    )
    assert not any(line.startswith("camp: resume it with:") for line in lines)


def test_no_declared_account_with_no_harness_keeps_todays_no_harness_line(tmp_path):
    """The control case: undeclared account, no harness — today's behavior
    (the fixed `_NO_HARNESS_LINE`) must be unchanged."""
    ws = _mkws(tmp_path)
    entry = _conv_entry(cwd="member")
    (decision,) = plan_resurrection(
        [entry], ws, env={"HOME": str(tmp_path)}, harness=None, group=GROUP_NO_ACCOUNT
    )
    lines = _lines_of(decision)
    assert lines == [
        f"camp: this window held conversation {entry.conversation_id}",
        "camp: no harness is configured for this group, so camp cannot compose a resume command",
    ]
