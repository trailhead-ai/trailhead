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
        switch_client_unanswered: bool = False,
        unanswered_reason: str = "no such file or directory",
    ) -> None:
        self._present = present
        self._reprobe = present if reprobe is None else reprobe
        self._create_returncode = create_returncode
        self._create_stderr = create_stderr
        self._switch_client_returncode = switch_client_returncode
        self._switch_client_unanswered = switch_client_unanswered
        self._unanswered_reason = unanswered_reason
        self.has_session_calls: list[str] = []
        self.new_session_calls: list[dict[str, object]] = []
        self.switch_client_calls: list[str] = []

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

    def switch_client(self, name: str):
        self.switch_client_calls.append(name)
        if self._switch_client_unanswered:
            return None
        return subprocess.CompletedProcess(
            args=["tmux"], returncode=self._switch_client_returncode, stdout="", stderr=""
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
    camp_cli, group_env, monkeypatch
):
    """`camp new` reaches the switch-client arm through the same handover
    `camp attach` uses, and its own failure must surface here too — a
    handover can fail after the outcome line already printed."""
    g = dict(group_env)
    g["env"] = {**g["env"], "TMUX": "/tmp/tmux-1000/default,1234,0"}
    tmux = _DoorTmux(present=False, switch_client_returncode=5)
    _wire_tmux(monkeypatch, tmux)
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    fake_stdout = _FakeTTY()
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    with pytest.raises(SystemExit) as exc:
        camp_cli._cmd_new_group_cli(["feat-inside-fail"], g["group"], g["env"], dry_run=False)

    assert exc.value.code == 5


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
