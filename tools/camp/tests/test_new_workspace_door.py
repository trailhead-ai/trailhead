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
    """

    def __init__(self, *, present: bool = False) -> None:
        self._present = present
        self.has_session_calls: list[str] = []
        self.new_session_calls: list[dict[str, object]] = []
        self.switch_client_calls: list[str] = []

    def has_session(self, name: str) -> bool | None:
        self.has_session_calls.append(name)
        return self._present

    def new_session(self, name, *, cwd, env=None, timeout=None):
        self.new_session_calls.append({"name": name, "cwd": cwd, "env": env})
        return subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr="")

    def switch_client(self, name: str):
        self.switch_client_calls.append(name)
        return subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr="")


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
