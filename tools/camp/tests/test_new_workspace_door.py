"""Test contract: `camp new <slug>` routes through the same door `camp attach`
opens onto its workspace's tmux session, and attaches by default.

Test contract (from
`task/camp-new-routes-through-the-door-and-attaches-by-default`):

- `camp new <slug>` with a terminal creates the workspace, creates its
  session, and reaches the exec seam once with the attach argv. Assert the
  argv, not only that the seam was called.
- `camp new <slug> --no-attach` creates both and never reaches the exec seam
  — inject a raiser.
- `camp new <slug> --no-session` creates the workspace, creates NO session,
  and reaches no exec seam. Assert the tmux seam saw no create call.
- `camp new <slug> --launch` behaves identically to the bare form and
  additionally prints the notice. Vary: with and without the flag, the
  session outcome is the same and only stderr differs.
- Without a terminal, all four forms create what they are meant to, report,
  exit 0, and reach no exec seam.
- `camp new <slug> --json` without `--launch` succeeds and emits the object,
  where today it refuses. Pin it by asserting the object, not by asserting
  an absence.
- Stdout is exactly the path line on every form; the outcome line and the
  notice are on stderr. Assert the two streams separately.
- The concierge's exact invocation succeeds and emits an object carrying the
  keys that skill reads, and `test_concierge_skill_conformance.py` stays
  green (see that file's own `group_env`/`_FakeConciergeTmux` fixtures).

No real tmux or exec is ever touched: the tmux seam
(`camp.launch.stop.Tmux`, the same factory attribute
`_open_workspace_door`'s own tests monkeypatch) and the exec handoff seam
(`camp.host.handoff.handoff`) are both injected fakes.
"""
from __future__ import annotations

import importlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from ._helpers import init_git_repo

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _load_cli_module():
    return importlib.import_module("camp.cli.group")


@pytest.fixture()
def camp_cli():
    return _load_cli_module()


@pytest.fixture()
def group_env(tmp_path):
    repo_a = tmp_path / "repo_a"
    init_git_repo(repo_a)
    group = {
        "group": {"name": "g"},
        "members": [
            {"name": "repo_a", "repo_root": str(repo_a), "bootstrap": [], "base": "origin/main"}
        ],
        "branch_pattern": "worktree-{slug}",
    }
    env = {
        "CAMP_STATE_DIR": str(tmp_path / "state"),
        "HOME": str(tmp_path / "home"),
    }
    return {"group": group, "env": env, "tmp_path": tmp_path}


@pytest.fixture(autouse=True)
def _stub_spawn(monkeypatch):
    """Never spawn a real detached provisioner in these tests."""
    import camp.provision.provision as provision

    monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class _DoorTmux:
    """A tmux stand-in exposing exactly `has_session` and `new_session` — the
    two calls the door dispatch and `create_workspace_session` issue.
    `switch_client` is included for completeness; the tests here never set
    `TMUX`, so the exec (attach-session) arm is the one exercised.

    `present` is the FIRST `has_session` answer; `reprobe` is every answer
    after the first (the unrecognised-create-failure re-probe) — `None`
    means "same as `present`", matching `test_attach_door_dispatch.py`'s
    own double so the two failure-arm fixtures share one shape.
    """

    def __init__(
        self,
        *,
        present: bool | None = False,
        reprobe: bool | None = None,
        create_returncode: int = 0,
        create_stderr: str = "",
        switch_client_returncode: int = 0,
        switch_client_stderr: str = "",
        switch_client_unanswered: bool = False,
        unanswered_reason: str = "no such file or directory",
        list_windows_answer: object = None,
        first_window: object = None,
        window_answers: object = (),
    ) -> None:
        self._present = present
        self._reprobe = present if reprobe is None else reprobe
        self._create_returncode = create_returncode
        self._create_stderr = create_stderr
        self._switch_client_returncode = switch_client_returncode
        self._switch_client_stderr = switch_client_stderr
        self._switch_client_unanswered = switch_client_unanswered
        self._unanswered_reason = unanswered_reason
        self._list_windows_answer = list_windows_answer
        self._first_window = first_window
        self._window_answers = list(window_answers)
        self.has_session_calls: list[str] = []
        self.new_session_calls: list[dict[str, object]] = []
        self.new_session_with_window_calls: list[dict[str, object]] = []
        self.new_window_calls: list[dict[str, object]] = []
        self.switch_client_calls: list[str] = []
        self.set_option_calls: list[dict[str, object]] = []
        self.install_binding_calls: list[str] = []
        self.list_windows_calls: list[str] = []

    def list_windows(self, name: str):
        self.list_windows_calls.append(name)
        if self._list_windows_answer is not None:
            return self._list_windows_answer
        from camp.launch.tmux import WindowListing

        return WindowListing(windows=(), dropped=0)

    def has_session(self, name: str) -> bool | None:
        self.has_session_calls.append(name)
        return self._present if len(self.has_session_calls) == 1 else self._reprobe

    def has_session_with_reason(self, name: str) -> tuple[bool | None, str | None]:
        present = self.has_session(name)
        return present, (self._unanswered_reason if present is None else None)

    def new_session(self, name, *, cwd, env=None, timeout=None):
        self.new_session_calls.append({"name": name, "cwd": cwd, "env": env})
        return subprocess.CompletedProcess(
            args=["tmux"],
            returncode=self._create_returncode,
            stdout="",
            stderr=self._create_stderr,
        )

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

    def set_option(self, target, key, value, *, timeout=None):
        self.set_option_calls.append({"target": target, "key": key, "value": value})
        return subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr="")

    def list_window_binding(self):
        return None

    def install_window_binding(self, true_command, *, timeout=None):
        self.install_binding_calls.append(true_command)
        return subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr="")

    def switch_client(self, name: str):
        self.switch_client_calls.append(name)
        if self._switch_client_unanswered:
            return None
        return subprocess.CompletedProcess(
            args=["tmux"],
            returncode=self._switch_client_returncode,
            stdout="",
            stderr=self._switch_client_stderr,
        )


class _RaisingTmux:
    """A tmux double for the `--no-session` regression: fails the test the
    instant EITHER method is called at all, rather than answering it —
    `--no-session` must never reach the door's dispatch in the first place.
    """

    def has_session(self, name: str) -> bool:
        raise AssertionError("--no-session must never probe the tmux seam")

    def new_session(self, *a, **k):
        raise AssertionError("--no-session must never reach the door's create call")


def _wire_tmux(monkeypatch, tmux) -> None:
    import camp.launch.stop as stop_module

    monkeypatch.setattr(stop_module, "Tmux", lambda *a, **k: tmux)


def _derived_name(group_name: str, slug: str) -> str:
    from camp.launch.naming import workspace_session_name

    return workspace_session_name(group_name, slug)


def _raising_handoff(monkeypatch):
    """Inject an exec seam that fails the test the instant it is reached."""
    import camp.host.handoff as handoff

    monkeypatch.setattr(
        handoff,
        "handoff",
        lambda argv: (_ for _ in ()).throw(
            AssertionError(f"the exec seam must never be reached, got argv={argv}")
        ),
    )


# ---------------------------------------------------------------------------
# With a terminal: bare form reaches the exec seam with the attach argv
# ---------------------------------------------------------------------------


def test_bare_with_a_terminal_creates_and_reaches_exec_seam_with_attach_argv(
    camp_cli, group_env, monkeypatch
):
    import camp.host.handoff as handoff

    g = group_env
    tmux = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux)
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))
    fake_stdin = _FakeTTY()
    fake_stdout = _FakeTTY()
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    with pytest.raises(SystemExit) as exc:
        camp_cli._cmd_new_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)

    assert exc.value.code == 0
    derived = _derived_name("g", "feat-x")
    assert seen == [["tmux", "attach-session", "-t", f"={derived}"]], (
        "the exec seam must be reached exactly once with the attach-session argv"
    )
    assert len(tmux.new_session_calls) == 1, "exactly one create attempt"
    assert tmux.switch_client_calls == []


# ---------------------------------------------------------------------------
# Inside tmux: the switch-client arm, including its own failure
# ---------------------------------------------------------------------------


def test_bare_inside_tmux_reaches_switch_client_never_the_exec_seam(
    camp_cli, group_env, monkeypatch
):
    g = dict(group_env)
    g["env"] = {**g["env"], "TMUX": "/tmp/tmux-1000/default,1234,0"}
    tmux = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux)
    _raising_handoff(monkeypatch)
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    monkeypatch.setattr(sys, "stdout", _FakeTTY())

    with pytest.raises(SystemExit) as exc:
        camp_cli._cmd_new_group_cli(["feat-inside"], g["group"], g["env"], dry_run=False)

    assert exc.value.code == 0
    derived = _derived_name("g", "feat-inside")
    assert tmux.switch_client_calls == [derived]


def test_inside_tmux_switch_client_failure_exits_with_its_own_returncode(
    camp_cli, group_env, monkeypatch, capsys
):
    """`camp new` reaches the switch-client arm through the same handover
    `camp attach` uses, and its own failure must surface here too — a
    handover can fail after the outcome line already printed, and tmux's
    own stderr must reach the operator rather than a silent nonzero exit."""
    g = dict(group_env)
    g["env"] = {**g["env"], "TMUX": "/tmp/tmux-1000/default,1234,0"}
    tmux = _DoorTmux(
        present=False, switch_client_returncode=5, switch_client_stderr="no current client\n"
    )
    _wire_tmux(monkeypatch, tmux)
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    fake_stdout = _FakeTTY()
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    with pytest.raises(SystemExit) as exc:
        camp_cli._cmd_new_group_cli(["feat-inside-fail"], g["group"], g["env"], dry_run=False)

    assert exc.value.code == 5
    assert "no current client" in capsys.readouterr().err


def test_inside_tmux_switch_client_unanswered_exits_one(
    camp_cli, group_env, monkeypatch
):
    g = dict(group_env)
    g["env"] = {**g["env"], "TMUX": "/tmp/tmux-1000/default,1234,0"}
    tmux = _DoorTmux(present=False, switch_client_unanswered=True)
    _wire_tmux(monkeypatch, tmux)
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    fake_stdout = _FakeTTY()
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    with pytest.raises(SystemExit) as exc:
        camp_cli._cmd_new_group_cli(["feat-inside-none"], g["group"], g["env"], dry_run=False)

    assert exc.value.code == 1


@pytest.mark.parametrize(
    "injected_stderr",
    [
        "no current client\nfake: forged a second line",
        "no current client\rfake: overwrote the line",
    ],
    ids=["embedded-newline", "embedded-carriage-return"],
)
def test_switch_client_stderr_carrying_control_characters_is_neutralized(
    camp_cli, group_env, monkeypatch, capsys, injected_stderr
):
    """The switch-client failure arm prints tmux's own stderr verbatim —
    hardened like the door's success line, so an embedded newline cannot
    forge a second stderr row and an embedded carriage return cannot
    rewrite it. Varied across two distinct control characters so the test
    depends on the input, not merely on the failure arm being taken."""
    g = dict(group_env)
    g["env"] = {**g["env"], "TMUX": "/tmp/tmux-1000/default,1234,0"}
    tmux = _DoorTmux(present=False, switch_client_returncode=5, switch_client_stderr=injected_stderr)
    _wire_tmux(monkeypatch, tmux)
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    monkeypatch.setattr(sys, "stdout", _FakeTTY())

    with pytest.raises(SystemExit) as exc:
        camp_cli._cmd_new_group_cli(["feat-inside-ctrl"], g["group"], g["env"], dry_run=False)

    assert exc.value.code == 5
    err = capsys.readouterr().err
    lines = [line for line in err.split("\n") if line]
    switch_lines = [line for line in lines if "no current client" in line]
    assert len(switch_lines) == 1, (
        f"an embedded control character must not forge a second line for "
        f"the switch-client diagnostic: {err!r}"
    )
    switch_line = switch_lines[0]
    assert "\r" not in switch_line, f"a raw carriage return reached the line: {switch_line!r}"
    assert "forged a second line" in switch_line or "overwrote the line" in switch_line


# ---------------------------------------------------------------------------
# --no-attach: creates both, never reaches the exec seam
# ---------------------------------------------------------------------------


def test_no_attach_creates_session_but_never_reaches_exec_seam(
    camp_cli, group_env, monkeypatch, capsys
):
    g = group_env
    tmux = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux)
    _raising_handoff(monkeypatch)
    fake_stdin = _FakeTTY()
    fake_stdout = _FakeTTY()
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    # Must return normally — no exec, no SystemExit from the handover arm.
    camp_cli._cmd_new_group_cli(["feat-x", "--no-attach"], g["group"], g["env"], dry_run=False)

    assert len(tmux.new_session_calls) == 1, "the session is still created"
    assert tmux.switch_client_calls == []


# ---------------------------------------------------------------------------
# --no-session: creates the workspace, no session, no exec seam
# ---------------------------------------------------------------------------


def test_no_session_creates_workspace_only_no_tmux_call_at_all(
    camp_cli, group_env, monkeypatch, capsys
):
    g = group_env
    tmux = _RaisingTmux()
    _wire_tmux(monkeypatch, tmux)
    _raising_handoff(monkeypatch)

    # No fake TTY needed: --no-session skips the door before interactivity
    # is even consulted for the handover.
    camp_cli._cmd_new_group_cli(["feat-x", "--no-session"], g["group"], g["env"], dry_run=False)

    out = capsys.readouterr().out
    assert out.strip().endswith("/feat-x"), out


# ---------------------------------------------------------------------------
# --launch: identical outcome to bare, differs only on stderr
# ---------------------------------------------------------------------------


def test_launch_flag_behaves_identically_to_bare_form_only_stderr_differs(
    camp_cli, group_env, monkeypatch, capsys
):
    g = group_env

    tmux_bare = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux_bare)
    camp_cli._cmd_new_group_cli(
        ["feat-bare", "--json"], g["group"], g["env"], dry_run=False
    )
    bare_captured = capsys.readouterr()
    bare_payload = json.loads(bare_captured.out)

    tmux_launch = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux_launch)
    camp_cli._cmd_new_group_cli(
        ["feat-launch", "--launch", "--json"], g["group"], g["env"], dry_run=False
    )
    launch_captured = capsys.readouterr()
    launch_payload = json.loads(launch_captured.out)

    # Same session outcome shape (only the slug/derived name differ, since
    # they are different slugs) — both "created", both unattached (no tty).
    assert bare_payload["outcome"] == launch_payload["outcome"] == "created"
    assert bare_payload["attached"] == launch_payload["attached"] is False
    assert len(tmux_bare.new_session_calls) == len(tmux_launch.new_session_calls) == 1

    assert "no longer needed" not in bare_captured.err
    assert "no longer needed" in launch_captured.err, (
        "only --launch prints the notice"
    )


# ---------------------------------------------------------------------------
# Without a terminal: all four forms create what they're meant to, exit 0,
# and never reach the exec seam.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra_args",
    [[], ["--no-attach"], ["--launch"], ["--json"]],
    ids=["bare", "no-attach", "launch", "json"],
)
def test_non_interactive_forms_create_report_and_never_exec(
    camp_cli, group_env, monkeypatch, capsys, extra_args
):
    g = group_env
    tmux = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux)
    _raising_handoff(monkeypatch)

    # pytest's own captured stdin/stdout are not ttys, so `interactive` is
    # already False here without any monkeypatching.
    camp_cli._cmd_new_group_cli(
        [f"feat-{'-'.join(a.strip('-') for a in extra_args) or 'plain'}", *extra_args],
        g["group"],
        g["env"],
        dry_run=False,
    )

    assert len(tmux.new_session_calls) == 1
    assert tmux.switch_client_calls == []


def test_no_session_form_creates_workspace_and_no_exec_with_no_terminal(
    camp_cli, group_env, monkeypatch, capsys
):
    g = group_env
    tmux = _RaisingTmux()
    _wire_tmux(monkeypatch, tmux)
    _raising_handoff(monkeypatch)

    camp_cli._cmd_new_group_cli(
        ["feat-ns", "--no-session"], g["group"], g["env"], dry_run=False
    )
    out = capsys.readouterr().out
    assert out.strip().endswith("/feat-ns")


# ---------------------------------------------------------------------------
# --json without --launch: succeeds and emits the object
# ---------------------------------------------------------------------------


def test_json_without_launch_succeeds_and_emits_the_door_object(
    camp_cli, group_env, monkeypatch, capsys
):
    g = group_env
    tmux = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux)

    camp_cli._cmd_new_group_cli(["feat-j", "--json"], g["group"], g["env"], dry_run=False)

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["outcome"] == "created"
    assert payload["slug"] == "feat-j"
    assert payload["group"] == "g"
    assert payload["attached"] is False


# ---------------------------------------------------------------------------
# Stream separation: stdout is exactly the path; outcome + notice on stderr
# ---------------------------------------------------------------------------


def test_stdout_is_exactly_the_path_stderr_carries_outcome_and_notice(
    camp_cli, group_env, monkeypatch, capsys
):
    from camp.group.manifest import workspace_dir

    g = group_env
    tmux = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux)

    camp_cli._cmd_new_group_cli(
        ["feat-s", "--launch"], g["group"], g["env"], dry_run=False
    )

    captured = capsys.readouterr()
    ws_dir = workspace_dir("g", "feat-s", env=g["env"])
    assert captured.out == f"{ws_dir}\n", "stdout must be exactly the path line"
    derived = _derived_name("g", "feat-s")
    assert f"created {derived}" in captured.err, "the outcome line belongs on stderr"
    assert "no longer needed" in captured.err, "the --launch notice belongs on stderr"


# ---------------------------------------------------------------------------
# The concierge's exact invocation
# ---------------------------------------------------------------------------


def test_concierge_exact_invocation_succeeds_and_emits_expected_keys(
    camp_cli, group_env, monkeypatch, capsys
):
    """Mirrors `test_concierge_skill_conformance.py`'s own coverage of this
    exact call — reproduced here directly against the door dispatch, not
    only through the skill-document conformance harness."""
    g = group_env
    tmux = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux)

    camp_cli._cmd_new_group_cli(
        ["feat-concierge", "--launch", "--no-wait", "--json"],
        g["group"],
        g["env"],
        dry_run=False,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert set(payload) == {
        "ok",
        "outcome",
        "slug",
        "group",
        "workspace_path",
        "tmux_session",
        "attached",
    }
    assert payload["attached"] is False, "the concierge's call carries no terminal"


# ---------------------------------------------------------------------------
# Operator decision at Phase 1: `camp new` succeeds with a warning when its
# session can't be created — the workspace is real and usable, so this is
# not a refusal. Both failure arms (`has_session` unanswered, create failed)
# take this path; `camp attach`'s own door is untouched and still refuses
# (pinned separately in `test_attach_door_dispatch.py`).
#
# `_run_capturing_exit` covers both a bare `return` (this codebase's own
# convention for a non-interactive success, e.g.
# `test_json_without_launch_succeeds_and_emits_the_door_object` above) and an
# explicit `sys.exit` — pinning "exits 0" must hold under either shape,
# since the pre-fix code takes the `sys.exit(1)` shape and the fix may or
# may not choose to exit explicitly.
# ---------------------------------------------------------------------------


def _run_capturing_exit(fn, *args, **kwargs) -> int:
    try:
        fn(*args, **kwargs)
    except SystemExit as exc:
        return 0 if exc.code is None else exc.code
    return 0


def test_tmux_unreachable_exits_zero_with_path_on_stdout_and_warning_on_stderr(
    camp_cli, group_env, monkeypatch, capsys
):
    from camp.group.manifest import workspace_dir

    g = group_env
    tmux = _DoorTmux(present=None)
    _wire_tmux(monkeypatch, tmux)

    code = _run_capturing_exit(
        camp_cli._cmd_new_group_cli, ["feat-unreachable"], g["group"], g["env"], dry_run=False
    )

    assert code == 0
    captured = capsys.readouterr()
    ws_dir = workspace_dir("g", "feat-unreachable", env=g["env"])
    assert captured.out.rstrip("\n").splitlines()[-1] == str(ws_dir), (
        "stdout's final line is exactly the workspace path"
    )
    derived = _derived_name("g", "feat-unreachable")
    assert derived in captured.err, "the warning names the unreached session"
    assert tmux.new_session_calls == [], "tmux never answered, so no create was attempted"


@pytest.mark.parametrize(
    "injected_reason",
    ["[Errno 2] No such file or directory: 'tmux'", "Command '['tmux', ...]' timed out after 5 seconds"],
    ids=["unlaunchable", "timeout"],
)
def test_tmux_unreachable_warning_carries_tmuxs_own_words(
    camp_cli, group_env, monkeypatch, capsys, injected_reason
):
    """Varies the injected reason across two distinct values and asserts
    each one reaches the stderr warning verbatim — the test must depend on
    the input, not merely on the failure arm being taken."""
    g = group_env
    tmux = _DoorTmux(present=None, unanswered_reason=injected_reason)
    _wire_tmux(monkeypatch, tmux)

    code = _run_capturing_exit(
        camp_cli._cmd_new_group_cli, ["feat-reason"], g["group"], g["env"], dry_run=False
    )

    assert code == 0
    captured = capsys.readouterr()
    assert injected_reason in captured.err, captured.err


@pytest.mark.parametrize(
    "injected_stderr",
    ["error connecting to /tmp/x (permission denied)", "no server running on socket /tmp/y"],
    ids=["permission-denied", "no-server"],
)
def test_create_failure_exits_zero_with_path_on_stdout_and_reason_on_stderr(
    camp_cli, group_env, monkeypatch, capsys, injected_stderr
):
    """Varies the injected tmux error text across two distinct values and
    asserts each one reaches stderr verbatim — the test must depend on the
    input, not merely on the failure arm being taken."""
    from camp.group.manifest import workspace_dir

    g = group_env
    tmux = _DoorTmux(present=False, create_returncode=1, create_stderr=injected_stderr)
    _wire_tmux(monkeypatch, tmux)

    code = _run_capturing_exit(
        camp_cli._cmd_new_group_cli, ["feat-createfail"], g["group"], g["env"], dry_run=False
    )

    assert code == 0
    captured = capsys.readouterr()
    ws_dir = workspace_dir("g", "feat-createfail", env=g["env"])
    assert captured.out.rstrip("\n").splitlines()[-1] == str(ws_dir)
    assert injected_stderr in captured.err, "tmux's own words reach stderr, varying with input"


@pytest.mark.parametrize(
    "injected_stderr",
    [
        "disk full\nfake: forged a second line",
        "disk full\rfake: overwrote the line",
    ],
    ids=["embedded-newline", "embedded-carriage-return"],
)
def test_create_failure_stderr_carrying_control_characters_is_neutralized(
    camp_cli, group_env, monkeypatch, capsys, injected_stderr
):
    """The workspace-only warning line prints tmux's own stderr verbatim —
    hardened like the door's success line, so an embedded newline cannot
    forge a second stderr row and an embedded carriage return cannot
    rewrite it. Varied across two distinct control characters so the test
    depends on the input, not merely on the failure arm being taken."""
    tmux = _DoorTmux(present=False, create_returncode=1, create_stderr=injected_stderr)
    _wire_tmux(monkeypatch, tmux)

    code = _run_capturing_exit(
        camp_cli._cmd_new_group_cli, ["feat-createctrl"], group_env["group"], group_env["env"], dry_run=False
    )

    assert code == 0
    err = capsys.readouterr().err
    lines = [line for line in err.split("\n") if line]
    warning_lines = [line for line in lines if "disk full" in line]
    assert len(warning_lines) == 1, (
        f"an embedded control character must not forge a second warning line: {err!r}"
    )
    warning_line = warning_lines[0]
    assert "\r" not in warning_line, f"a raw carriage return reached the line: {warning_line!r}"
    assert "forged a second line" in warning_line or "overwrote the line" in warning_line


class _RaisingCreateDoorTmux(_DoorTmux):
    """A `_DoorTmux` whose `new_session` raises instead of answering — the
    shape a wedged tmux server-start (a plugin-heavy `tmux.conf`, a loaded
    machine) takes at the one call that starts the tmux SERVER."""

    def __init__(self, *, exc: BaseException, **kwargs) -> None:
        super().__init__(**kwargs)
        self._exc = exc

    def new_session(self, name, *, cwd, env=None, timeout=None):
        self.new_session_calls.append({"name": name, "cwd": cwd, "env": env})
        raise self._exc


def test_a_create_call_that_raises_reports_workspace_only_never_a_traceback(
    camp_cli, group_env, monkeypatch, capsys
):
    """`camp new` must never surface a raw traceback for a create call that
    times out or hits an unlaunchable tmux — the workspace already exists on
    disk, so it reports workspace-only, exit 0, carrying the exception's own
    message and the session it could not create."""
    g = group_env
    exc = subprocess.TimeoutExpired(cmd=["tmux", "new-session"], timeout=5)
    tmux = _RaisingCreateDoorTmux(present=False, exc=exc)
    _wire_tmux(monkeypatch, tmux)

    code = _run_capturing_exit(
        camp_cli._cmd_new_group_cli, ["feat-createraise"], g["group"], g["env"], dry_run=False
    )

    assert code == 0
    captured = capsys.readouterr()
    assert str(exc) in captured.err
    assert _derived_name("g", "feat-createraise") in captured.err
    assert len(tmux.new_session_calls) == 1


@pytest.mark.parametrize(
    "tmux_factory",
    [
        lambda: _DoorTmux(present=None),
        lambda: _DoorTmux(present=False, create_returncode=1, create_stderr="boom"),
    ],
    ids=["tmux-unreachable", "create-failed"],
)
def test_neither_failure_arm_reaches_exec_or_switch_client_seam(
    camp_cli, group_env, monkeypatch, capsys, tmux_factory
):
    g = group_env
    tmux = tmux_factory()
    _wire_tmux(monkeypatch, tmux)
    _raising_handoff(monkeypatch)
    fake_stdin = _FakeTTY()
    fake_stdout = _FakeTTY()
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    code = _run_capturing_exit(
        camp_cli._cmd_new_group_cli, ["feat-noexec"], g["group"], g["env"], dry_run=False
    )

    assert code == 0
    assert tmux.switch_client_calls == [], "the switch-client seam must never be reached"


@pytest.mark.parametrize(
    "tmux_factory",
    [
        lambda: _DoorTmux(present=None),
        lambda: _DoorTmux(present=False, create_returncode=1, create_stderr="disk full"),
    ],
    ids=["tmux-unreachable", "create-failed"],
)
def test_json_on_each_failure_arm_emits_exactly_one_workspace_only_object(
    camp_cli, group_env, monkeypatch, capsys, tmux_factory
):
    from camp.group.manifest import workspace_dir

    g = group_env
    tmux = tmux_factory()
    _wire_tmux(monkeypatch, tmux)

    code = _run_capturing_exit(
        camp_cli._cmd_new_group_cli, ["feat-jsonfail", "--json"], g["group"], g["env"], dry_run=False
    )

    assert code == 0
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 1, f"exactly one object on stdout, got: {lines!r}"
    payload = json.loads(lines[0])

    ws_dir = workspace_dir("g", "feat-jsonfail", env=g["env"])
    assert payload == {
        "ok": True,
        "outcome": "workspace-only",
        "slug": "feat-jsonfail",
        "group": "g",
        "workspace_path": str(ws_dir),
        "tmux_session": None,
        "attached": False,
        "session_error": payload.get("session_error"),
    }
    assert payload["session_error"], "session_error must carry the reason, non-empty"


def test_a_malformed_sibling_group_config_reports_workspace_only_never_tracebacks(
    camp_cli, group_env, monkeypatch, capsys
):
    """The credential-store gate's account union spans every group camp
    knows about, so it reads every group config — including one that has
    nothing to do with the workspace being created. A sibling group's
    unparseable config must surface as `camp new`'s own workspace-only
    warning, never a raw traceback (the door is the one place `LaunchError`
    was previously uncaught on this path). The workspace itself is real and
    usable either way, so this is the same exit-0 posture every other
    session-creation failure gets here — `camp attach`'s own door is the one
    that refuses on this condition (pinned separately in
    `test_attach_door_dispatch.py`)."""
    from trailhead.paths import config_dir

    g = group_env
    groups_dir = config_dir("camp", env=g["env"]) / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    (groups_dir / "broken.toml").write_text("not = [valid toml\n")
    tmux = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux)

    code = _run_capturing_exit(
        camp_cli._cmd_new_group_cli, ["feat-malformed"], g["group"], g["env"], dry_run=False
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "Traceback" not in captured.err
    assert captured.out.strip().endswith("/feat-malformed")
    assert tmux.switch_client_calls == []


# ---------------------------------------------------------------------------
# Security audit Fix 2: a policy refusal (credential-store) and a transient
# tmux failure share `CREATE_FAILED` no longer — `camp new` must be able to
# tell them apart in its `--json` object, not just in free text.
# ---------------------------------------------------------------------------


def test_a_credential_store_workspace_reports_a_distinct_outcome_and_wording(
    camp_cli, group_env, monkeypatch, capsys
):
    """A workspace directory that lands at, under, or above a credential
    store is a POLICY refusal, not tmux having a bad moment — it must reach
    stderr with wording distinct from the plain 'warning' a transient
    failure gets, exit 0 exactly like every other workspace-only case
    (the workspace itself is real and usable), and never reach a create
    call."""
    from trailhead.paths import config_dir

    g = dict(group_env)
    home = g["tmp_path"] / "home"
    g["env"] = {
        **g["env"],
        "HOME": str(home),
        "CAMP_STATE_DIR": str(home / ".ssh" / "state"),
    }
    config_dir("camp", env=g["env"]).mkdir(parents=True, exist_ok=True)
    tmux = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux)

    code = _run_capturing_exit(
        camp_cli._cmd_new_group_cli, ["feat-credstore"], g["group"], g["env"], dry_run=False
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "refused" in captured.err, captured.err
    assert "warning" not in captured.err, (
        "a policy refusal must not read as the same routine noise as a "
        f"transient tmux failure: {captured.err!r}"
    )
    assert tmux.new_session_calls == [], "the gate refuses before any create call reaches tmux"


def test_credential_store_and_transient_failure_emit_different_json_outcomes(
    camp_cli, group_env, monkeypatch, capsys
):
    """Varies the failure KIND (policy refusal vs. transient tmux failure)
    and asserts the `--json` `outcome` value changes with it — a consumer
    must be able to tell them apart without parsing `session_error`."""
    from trailhead.paths import config_dir

    g_refused = dict(group_env)
    home = g_refused["tmp_path"] / "home"
    g_refused["env"] = {
        **g_refused["env"],
        "HOME": str(home),
        "CAMP_STATE_DIR": str(home / ".ssh" / "state"),
    }
    config_dir("camp", env=g_refused["env"]).mkdir(parents=True, exist_ok=True)
    tmux_refused = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux_refused)

    code = _run_capturing_exit(
        camp_cli._cmd_new_group_cli,
        ["feat-credstore-json", "--json"],
        g_refused["group"],
        g_refused["env"],
        dry_run=False,
    )
    assert code == 0
    refused_payload = json.loads(capsys.readouterr().out)

    g_transient = group_env
    tmux_transient = _DoorTmux(present=False, create_returncode=1, create_stderr="disk full")
    _wire_tmux(monkeypatch, tmux_transient)

    code = _run_capturing_exit(
        camp_cli._cmd_new_group_cli,
        ["feat-transient-json", "--json"],
        g_transient["group"],
        g_transient["env"],
        dry_run=False,
    )
    assert code == 0
    transient_payload = json.loads(capsys.readouterr().out)

    assert refused_payload["outcome"] != transient_payload["outcome"], (
        "a policy refusal and a transient failure must render different "
        f"outcome values, got {refused_payload['outcome']!r} for both"
    )
    assert refused_payload["outcome"] == "workspace-only-refused"
    assert transient_payload["outcome"] == "workspace-only"
    assert refused_payload["tmux_session"] is None
    assert refused_payload["attached"] is False


def test_concierge_invocation_exits_zero_on_a_failure_arm_with_documented_keys(
    camp_cli, group_env, monkeypatch, capsys
):
    """The concierge's exact documented invocation
    (`skills/concierge/SKILL.md:120`) must still succeed and emit an object
    when the session can't be created — the create path's deliverable is
    the workspace, not the session."""
    g = group_env
    tmux = _DoorTmux(present=None)
    _wire_tmux(monkeypatch, tmux)

    code = _run_capturing_exit(
        camp_cli._cmd_new_group_cli,
        ["feat-concierge-fail", "--launch", "--no-wait", "--json"],
        g["group"],
        g["env"],
        dry_run=False,
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["outcome"] == "workspace-only"
    assert "session_error" in payload


# ---------------------------------------------------------------------------
# camp new against an existing, running session reconciles the record too
# ---------------------------------------------------------------------------


def test_new_against_a_corrupt_record_reports_workspace_only_and_distinguishes_the_refusal(
    camp_cli, group_env, monkeypatch, capsys
):
    """`camp new`'s door folds `RECORD_UNREADABLE` into
    `_report_workspace_only(refused=True)` — the workspace already exists on
    disk and is usable, so this is exit 0 with a distinguishable refused
    wording, same posture as the credential-store policy refusal."""
    from camp.group.manifest import workspace_dir
    from camp.group.window_record import window_record_path_for

    g = group_env
    tmux = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux)
    camp_cli._cmd_new_group_cli(["feat-corrupt", "--no-attach"], g["group"], g["env"], dry_run=False)
    capsys.readouterr()

    ws_dir = workspace_dir("g", "feat-corrupt", env=g["env"])
    window_record_path_for(ws_dir).write_text("not json", encoding="utf-8")

    tmux2 = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux2)
    code = _run_capturing_exit(
        camp_cli._cmd_new_group_cli, ["feat-corrupt", "--no-attach"], g["group"], g["env"], dry_run=False
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "refused" in captured.err
    assert tmux2.new_session_calls == []
    assert tmux2.new_session_with_window_calls == []


def test_new_against_a_corrupt_record_json_reports_workspace_only_refused(
    camp_cli, group_env, monkeypatch, capsys
):
    from camp.group.manifest import workspace_dir
    from camp.group.window_record import window_record_path_for

    g = group_env
    tmux = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux)
    camp_cli._cmd_new_group_cli(["feat-corrupt-json", "--no-attach"], g["group"], g["env"], dry_run=False)
    capsys.readouterr()

    ws_dir = workspace_dir("g", "feat-corrupt-json", env=g["env"])
    window_record_path_for(ws_dir).write_text("not json", encoding="utf-8")

    tmux2 = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux2)
    camp_cli._cmd_new_group_cli(
        ["feat-corrupt-json", "--no-attach", "--json"], g["group"], g["env"], dry_run=False
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["outcome"] == "workspace-only-refused"


def test_new_resurrects_a_workspace_with_a_gone_session_and_a_surviving_record(
    camp_cli, group_env, monkeypatch, capsys
):
    from camp.group.manifest import workspace_dir
    from camp.group.window_record import WindowEntry, window_record_path_for, write_window_record
    from camp.launch.tmux import NewWindowResult

    g = group_env
    tmux = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux)
    camp_cli._cmd_new_group_cli(["feat-res", "--no-attach"], g["group"], g["env"], dry_run=False)
    capsys.readouterr()

    ws_dir = workspace_dir("g", "feat-res", env=g["env"])
    (ws_dir / "one").mkdir()
    write_window_record(
        window_record_path_for(ws_dir),
        [WindowEntry(window_id="@1", name="one", cwd="one", conversation_id="c1")],
    )

    tmux2 = _DoorTmux(present=False, first_window=NewWindowResult(window_id="@10", window_name="one"))
    _wire_tmux(monkeypatch, tmux2)
    code = _run_capturing_exit(
        camp_cli._cmd_new_group_cli,
        ["feat-res", "--no-attach", "--json"],
        g["group"],
        g["env"],
        dry_run=False,
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["outcome"] == "resurrected"
    assert payload["windows"] == {"restored": 1, "failed": 0, "dropped": 0}
    assert tmux2.new_session_calls == []
    assert len(tmux2.new_session_with_window_calls) == 1


def test_new_against_a_running_session_with_a_stale_record_prints_dropped_on_stderr(
    camp_cli, group_env, monkeypatch, capsys
):
    """`create_or_connect_workspace_session` reconciles on every CONNECTED
    fold and writes the corrected record, but `camp new`'s door never
    printed what it found — unlike `camp attach`, which reports it through
    `_print_reconcile_outcome`. `camp new` against an existing workspace
    whose session is already running, carrying a stale record entry, must
    print the same `dropped @N` line on stderr and correct the record."""
    from camp.group.manifest import workspace_dir
    from camp.group.window_record import (
        WindowEntry,
        read_window_record,
        window_record_path_for,
        write_window_record,
    )

    g = group_env
    tmux = _DoorTmux(present=False)
    _wire_tmux(monkeypatch, tmux)
    camp_cli._cmd_new_group_cli(["feat-s", "--no-attach"], g["group"], g["env"], dry_run=False)
    capsys.readouterr()  # discard the create call's own output

    ws_dir = workspace_dir("g", "feat-s", env=g["env"])
    write_window_record(
        window_record_path_for(ws_dir),
        [WindowEntry(window_id="@9", name="gone", cwd=".", conversation_id="dead-conv")],
    )

    tmux2 = _DoorTmux(present=True)
    _wire_tmux(monkeypatch, tmux2)

    camp_cli._cmd_new_group_cli(["feat-s", "--no-attach"], g["group"], g["env"], dry_run=False)
    captured = capsys.readouterr()

    assert "dropped @9" in captured.err
    reread = read_window_record(window_record_path_for(ws_dir))
    assert [e.window_id for e in reread.entries] == []
