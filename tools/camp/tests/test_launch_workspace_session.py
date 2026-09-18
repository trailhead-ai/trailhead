"""Tests for launch/workspace_session.py — the function that turns a
resolved workspace into a live tmux session.

Test contract (task/the-seam-creates-a-workspace-session-rooted-at-the-workspace):
- Creating against a name no session holds produces a session at exactly
  that name, rooted at the workspace directory, varied across two different
  workspace directories.
- The derived name varies with the group; creating in one leaves the
  other's session untouched.
- Creating against a name that already exists returns the already-existed
  answer, not the created one, and issues no second create — driven
  through a fake tmux whose `new_session` fails with the duplicate line.
- A create that fails for any other reason returns the failed answer
  carrying tmux's own stderr verbatim, varied across two different stderr
  strings.
- A workspace directory at or under a credential store is refused and no
  session is created.
- (The `-s`-unprefixed / `-t`-qualified property re-asserted at this call
  site is pinned in `test_launch_tmux.py`, against the seam's own
  `new_session`, which this module's only tmux call goes through.)

Every test here drives `create_workspace_session` against a hand-rolled
fake `Tmux` stand-in that records what it was called with — this module
never builds its own `["tmux", ...]` argv, so there is nothing for a fake
subprocess to intercept; what there IS to test is that this function calls
the seam with the right name, the right cwd, and interprets the seam's
answer correctly.
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


class _FakeTmux:
    """Records every `new_session` call and answers with a fixed result."""

    def __init__(self, *, returncode: int = 0, stderr: str = "") -> None:
        self._returncode = returncode
        self._stderr = stderr
        self.calls: list[dict[str, object]] = []

    def new_session(self, name, *, cwd, env=None, timeout=None):
        self.calls.append({"name": name, "cwd": cwd, "env": env, "timeout": timeout})
        return subprocess.CompletedProcess(
            args=["tmux"],
            returncode=self._returncode,
            stdout="",
            stderr=self._stderr,
        )


def test_the_create_call_carries_a_budget_wide_enough_to_start_a_tmux_server(tmp_path):
    """`new_session` is the one call that starts the tmux SERVER when none is
    running yet — the same operation `camp launch`'s own spawn budgets 30s
    for (`_SPAWN_TIMEOUT_SECONDS`, `launch/session.py`). The seam's own
    default (`Tmux.__init__`'s 5s) is tuned for a quick existence probe, not
    a server bring-up, so this call states its own wider timeout rather than
    inheriting that default."""
    from camp.launch.workspace_session import create_workspace_session

    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()
    tmux = _FakeTmux()

    create_workspace_session("g", "feat-x", ws_dir, env={"HOME": str(tmp_path)}, tmux=tmux)

    assert len(tmux.calls) == 1
    assert tmux.calls[0]["timeout"] == 30
    assert tmux.calls[0]["timeout"] != 5.0, (
        "must not merely inherit the seam's short probe default"
    )


def test_creating_against_a_free_name_produces_a_session_at_that_name_rooted_at_the_workspace_dir(
    tmp_path,
):
    from camp.launch.workspace_session import (
        WorkspaceSessionOutcome,
        create_workspace_session,
    )
    from camp.launch.naming import workspace_session_name

    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home)}

    ws_dir_a = tmp_path / "workspace-a"
    ws_dir_a.mkdir()
    fake_a = _FakeTmux()
    result_a = create_workspace_session(
        "trailhead", "camp-cli", ws_dir_a, env=env, tmux=fake_a
    )

    ws_dir_b = tmp_path / "workspace-b"
    ws_dir_b.mkdir()
    fake_b = _FakeTmux()
    result_b = create_workspace_session(
        "trailhead", "camp-cli", ws_dir_b, env=env, tmux=fake_b
    )

    expected_name = workspace_session_name("trailhead", "camp-cli")
    assert result_a.outcome is WorkspaceSessionOutcome.CREATED
    assert result_a.session_name == expected_name
    assert fake_a.calls[0]["name"] == expected_name
    assert Path(fake_a.calls[0]["cwd"]) == ws_dir_a.resolve()

    assert result_b.outcome is WorkspaceSessionOutcome.CREATED
    assert Path(fake_b.calls[0]["cwd"]) == ws_dir_b.resolve()
    assert fake_a.calls[0]["cwd"] != fake_b.calls[0]["cwd"], (
        "rooting must follow the workspace directory passed in, not a "
        "default shared across calls"
    )


def test_the_derived_name_varies_with_the_group_and_creating_one_leaves_the_other_untouched(
    tmp_path,
):
    from camp.launch.workspace_session import create_workspace_session
    from camp.launch.naming import workspace_session_name

    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home)}
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()

    fake_a = _FakeTmux()
    fake_b = _FakeTmux()

    create_workspace_session("group-a", "camp-cli", ws_dir, env=env, tmux=fake_a)
    create_workspace_session("group-b", "camp-cli", ws_dir, env=env, tmux=fake_b)

    name_a = fake_a.calls[0]["name"]
    name_b = fake_b.calls[0]["name"]
    assert name_a != name_b
    assert name_a == workspace_session_name("group-a", "camp-cli")
    assert name_b == workspace_session_name("group-b", "camp-cli")
    assert len(fake_a.calls) == 1, "group-a's seam must not see group-b's create"
    assert len(fake_b.calls) == 1, "group-b's seam must not see group-a's create"


def test_a_free_name_that_races_to_duplicate_reports_already_existed_not_created(
    tmp_path,
):
    from camp.launch.workspace_session import (
        WorkspaceSessionOutcome,
        create_workspace_session,
    )

    home = tmp_path / "home"
    home.mkdir()
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()
    fake = _FakeTmux(
        returncode=1, stderr="duplicate session: camp-trailhead-camp-cli\n"
    )

    result = create_workspace_session(
        "trailhead", "camp-cli", ws_dir, env={"HOME": str(home)}, tmux=fake
    )

    assert result.outcome is WorkspaceSessionOutcome.ALREADY_EXISTED
    assert len(fake.calls) == 1, "a duplicate answer must not trigger a retry create"


@pytest.mark.parametrize(
    "stderr",
    [
        "sessions should be nested with care, unset TMUX to force\n",
        "error: unsafe socket directory\n",
    ],
)
def test_a_create_failure_for_any_other_reason_returns_failed_with_tmuxs_stderr_verbatim(
    tmp_path, stderr
):
    from camp.launch.workspace_session import (
        WorkspaceSessionOutcome,
        create_workspace_session,
    )

    home = tmp_path / "home"
    home.mkdir()
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()
    fake = _FakeTmux(returncode=1, stderr=stderr)

    result = create_workspace_session(
        "trailhead", "camp-cli", ws_dir, env={"HOME": str(home)}, tmux=fake
    )

    assert result.outcome is WorkspaceSessionOutcome.FAILED
    assert result.error == stderr, "the reported error must be tmux's own stderr, unsummarized"


def test_a_workspace_directory_under_a_credential_store_is_refused_before_any_session_is_created(
    tmp_path,
):
    from camp.launch.session import LaunchError
    from camp.launch.workspace_session import create_workspace_session

    home = tmp_path / "home"
    home.mkdir()
    ws_dir = home / ".ssh" / "sub"
    ws_dir.mkdir(parents=True)
    fake = _FakeTmux()

    with pytest.raises(LaunchError):
        create_workspace_session(
            "trailhead", "camp-cli", ws_dir, env={"HOME": str(home)}, tmux=fake
        )

    assert fake.calls == [], "no create attempt may reach tmux once the credential rule refuses"


class _FakeDoorTmux:
    """A tmux stand-in for `create_or_connect_workspace_session`, answering
    `has_session_with_reason` directly (never `None`-reason when present is
    not `None`, matching the real seam's own contract)."""

    def __init__(self, *, present: bool | None = False) -> None:
        self._present = present
        self.new_session_calls: list[dict[str, object]] = []

    def has_session_with_reason(self, name: str):
        return self._present, None

    def new_session(self, name, *, cwd, env=None, timeout=None):
        self.new_session_calls.append({"name": name, "cwd": cwd, "env": env})
        return subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr="")


def test_a_credential_store_launch_error_during_create_folds_into_create_refused_not_a_traceback(
    tmp_path,
):
    """`create_workspace_session` raises `LaunchError` unconditionally when
    the credential-store gate refuses — including when it cannot even be
    evaluated because a sibling group's config is unreadable. Both doors
    share `create_or_connect_workspace_session`, so it must catch that here
    once, folding it into `DoorState.CREATE_REFUSED` — a policy refusal,
    distinct from `DoorState.CREATE_FAILED`'s transient-failure vocabulary —
    with the exception's own message as the reason, rather than letting it
    propagate as a raw traceback to either caller."""
    from camp.launch.workspace_session import (
        DoorState,
        create_or_connect_workspace_session,
    )

    home = tmp_path / "home"
    ws_dir = home / ".ssh" / "sub"
    ws_dir.mkdir(parents=True)
    tmux = _FakeDoorTmux(present=False)

    probe = create_or_connect_workspace_session(
        "trailhead", "camp-cli", ws_dir, env={"HOME": str(home)}, tmux=tmux
    )

    assert probe.state is DoorState.CREATE_REFUSED
    assert probe.state is not DoorState.CREATE_FAILED
    assert "credential" in probe.reason.lower() or ".ssh" in probe.reason
    assert tmux.new_session_calls == [], "the gate refuses before any create call reaches tmux"


def test_a_credential_store_workspace_with_an_existing_session_is_connected_without_the_gate(
    tmp_path,
):
    """The connect arm returns from `has_session_with_reason` before
    `create_workspace_session` — and therefore the credential-store gate —
    is ever reached. A workspace directory under a credential store with a
    session already live at the derived name is connected to, not refused:
    the gate only ever stops camp from ROOTING a session in a credential
    store, and the connect arm roots nothing."""
    from camp.launch.workspace_session import (
        DoorState,
        create_or_connect_workspace_session,
    )

    home = tmp_path / "home"
    ws_dir = home / ".ssh" / "sub"
    ws_dir.mkdir(parents=True)
    tmux = _FakeDoorTmux(present=True)

    probe = create_or_connect_workspace_session(
        "trailhead", "camp-cli", ws_dir, env={"HOME": str(home)}, tmux=tmux
    )

    assert probe.state is DoorState.CONNECTED
    assert tmux.new_session_calls == [], "no create attempt is made on the connect arm"


class _RaisingCreateTmux:
    """A tmux stand-in whose `new_session` raises instead of answering —
    the shape the real `Tmux.new_session` (via `Tmux.spawn_session`) takes
    when the create call itself times out or the binary is unlaunchable,
    rather than completing with a non-zero exit."""

    def __init__(self, *, present: bool | None = False, exc: BaseException) -> None:
        self._present = present
        self._exc = exc
        self.new_session_calls: list[dict[str, object]] = []

    def has_session_with_reason(self, name: str):
        return self._present, None

    def new_session(self, name, *, cwd, env=None, timeout=None):
        self.new_session_calls.append({"name": name, "cwd": cwd, "env": env})
        raise self._exc


@pytest.mark.parametrize(
    "exc",
    [
        subprocess.TimeoutExpired(cmd=["tmux", "new-session"], timeout=5),
        FileNotFoundError("[Errno 2] No such file or directory: 'tmux'"),
    ],
    ids=["timeout", "unlaunchable"],
)
def test_a_create_call_that_raises_folds_into_create_failed_with_its_own_message(
    tmp_path, exc
):
    """`Tmux.new_session` is unwrapped in the real seam — a plugin-heavy
    tmux.conf or a loaded machine can raise `OSError` or
    `TimeoutExpired` starting the tmux server. That must fold into
    `CREATE_FAILED` carrying the exception's own message, exactly like the
    `LaunchError` fold above, rather than escape as a raw traceback to
    either door."""
    from camp.launch.workspace_session import (
        DoorState,
        create_or_connect_workspace_session,
    )

    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()
    tmux = _RaisingCreateTmux(present=False, exc=exc)

    probe = create_or_connect_workspace_session(
        "trailhead", "camp-cli", ws_dir, env={"HOME": str(tmp_path)}, tmux=tmux
    )

    assert probe.state is DoorState.CREATE_FAILED
    assert str(exc) in probe.reason
    assert len(tmux.new_session_calls) == 1


def test_tmux_unanswered_reason_carries_the_seams_own_words(tmp_path):
    """`create_or_connect_workspace_session`'s `TMUX_UNANSWERED` reason
    must vary with what tmux actually said, not a single fixed string —
    proven across two distinct injected reasons."""
    from camp.launch.workspace_session import (
        DoorState,
        create_or_connect_workspace_session,
    )

    class _UnansweredTmux:
        def __init__(self, reason: str) -> None:
            self._reason = reason

        def has_session_with_reason(self, name: str):
            return None, self._reason

    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()

    for reason in ("[Errno 2] No such file or directory: 'tmux'", "timed out after 5 seconds"):
        probe = create_or_connect_workspace_session(
            "trailhead", "camp-cli", ws_dir, env={"HOME": str(tmp_path)}, tmux=_UnansweredTmux(reason)
        )
        assert probe.state is DoorState.TMUX_UNANSWERED
        assert reason in probe.reason
        assert "camp list" in probe.reason
