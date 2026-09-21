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

    def __init__(
        self,
        *,
        returncode: int = 0,
        stderr: str = "",
        existing_binding: str | None = None,
        failing_option: str | None = None,
        failing_option_answer: object = "non-zero",
    ) -> None:
        self._returncode = returncode
        self._stderr = stderr
        self._existing_binding = existing_binding
        self._failing_option = failing_option
        self._failing_option_answer = failing_option_answer
        self.calls: list[dict[str, object]] = []
        self.set_option_calls: list[dict[str, object]] = []
        self.install_binding_calls: list[str] = []
        self.killed: list[str] = []

    def new_session(self, name, *, cwd, env=None, timeout=None):
        self.calls.append({"name": name, "cwd": cwd, "env": env, "timeout": timeout})
        return subprocess.CompletedProcess(
            args=["tmux"],
            returncode=self._returncode,
            stdout="",
            stderr=self._stderr,
        )

    def set_option(self, target, key, value, *, timeout=None):
        self.set_option_calls.append({"target": target, "key": key, "value": value})
        if key == self._failing_option:
            if self._failing_option_answer is None:
                return None
            return subprocess.CompletedProcess(
                args=["tmux"], returncode=1, stdout="", stderr="tmux: no such session"
            )
        return subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr="")

    def kill_session(self, name, *, timeout=None):
        self.killed.append(name)
        return subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr="")

    def list_window_binding(self):
        return self._existing_binding

    def install_window_binding(self, true_command, *, timeout=None):
        self.install_binding_calls.append(true_command)
        return subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr="")


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
    not `None`, matching the real seam's own contract).

    `list_windows_answer` is what the connect arm's reconciliation call
    gets back from `list_windows` — defaulting to an empty, answered
    listing so a test that does not care about reconciliation (most of the
    existing ones in this module) keeps working unchanged once the connect
    arm starts calling it. `list_windows_raises`, when set, makes
    `list_windows` fail the test instead of answering — used to prove the
    create arm never reaches it.
    """

    def __init__(
        self,
        *,
        present: bool | None = False,
        list_windows_answer: object = None,
        list_windows_raises: bool = False,
    ) -> None:
        self._present = present
        self._list_windows_answer = list_windows_answer
        self._list_windows_raises = list_windows_raises
        self.new_session_calls: list[dict[str, object]] = []
        self.list_windows_calls: list[str] = []

    def has_session_with_reason(self, name: str):
        return self._present, None

    def list_windows(self, name: str):
        self.list_windows_calls.append(name)
        if self._list_windows_raises:
            raise AssertionError("list_windows must not be called on the create arm")
        if self._list_windows_answer is not None:
            return self._list_windows_answer
        from camp.launch.tmux import WindowListing

        return WindowListing(windows=(), dropped=0)

    def new_session(self, name, *, cwd, env=None, timeout=None):
        self.new_session_calls.append({"name": name, "cwd": cwd, "env": env})
        return subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr="")

    def set_option(self, target, key, value, *, timeout=None):
        return subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr="")

    def list_window_binding(self):
        return None

    def install_window_binding(self, true_command, *, timeout=None):
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


def test_creating_a_session_marks_it_and_installs_the_window_binding(tmp_path):
    """AC17's create-time wiring: a CREATED session gets three session-local
    options — @camp_workspace, @camp_group, @camp_slug — and the
    server-global window binding is installed, varied across two distinct
    (group, slug) pairs so this is not a fixed-string echo."""
    from camp.launch.workspace_session import create_workspace_session
    from camp.launch.naming import workspace_session_name
    from camp.launch.tmux import target as tmux_target

    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home)}

    ws_dir_a = tmp_path / "workspace-a"
    ws_dir_a.mkdir()
    fake_a = _FakeTmux()
    create_workspace_session("trailhead", "camp-cli", ws_dir_a, env=env, tmux=fake_a)

    ws_dir_b = tmp_path / "workspace-b"
    ws_dir_b.mkdir()
    fake_b = _FakeTmux()
    create_workspace_session("acme", "feat-x", ws_dir_b, env=env, tmux=fake_b)

    name_a = workspace_session_name("trailhead", "camp-cli")
    options_a = {c["key"]: c["value"] for c in fake_a.set_option_calls}
    assert options_a == {"@camp_workspace": "1", "@camp_group": "trailhead", "@camp_slug": "camp-cli"}
    assert all(c["target"] == tmux_target(name_a) for c in fake_a.set_option_calls)
    assert len(fake_a.install_binding_calls) == 1

    name_b = workspace_session_name("acme", "feat-x")
    options_b = {c["key"]: c["value"] for c in fake_b.set_option_calls}
    assert options_b == {"@camp_workspace": "1", "@camp_group": "acme", "@camp_slug": "feat-x"}
    assert all(c["target"] == tmux_target(name_b) for c in fake_b.set_option_calls)
    assert len(fake_b.install_binding_calls) == 1

    assert options_a != options_b, "must vary with the actual (group, slug), not a fixed pair"


def test_an_already_existed_outcome_installs_no_new_options_or_binding(tmp_path):
    """A duplicate-session race means another camp process's own create
    already marked the session and installed the binding — this call must
    not repeat that work against a session it never created."""
    from camp.launch.workspace_session import create_workspace_session

    home = tmp_path / "home"
    home.mkdir()
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()
    fake = _FakeTmux(returncode=1, stderr="duplicate session: camp-trailhead-camp-cli\n")

    create_workspace_session("trailhead", "camp-cli", ws_dir, env={"HOME": str(home)}, tmux=fake)

    assert fake.set_option_calls == []
    assert fake.install_binding_calls == []


def test_a_failed_create_installs_no_options_or_binding(tmp_path):
    from camp.launch.workspace_session import create_workspace_session

    home = tmp_path / "home"
    home.mkdir()
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()
    fake = _FakeTmux(returncode=1, stderr="error: unsafe socket directory\n")

    create_workspace_session("trailhead", "camp-cli", ws_dir, env={"HOME": str(home)}, tmux=fake)

    assert fake.set_option_calls == []
    assert fake.install_binding_calls == []


def test_a_session_whose_mark_tmux_refused_is_not_reported_created(tmp_path):
    """The three session-local options are what MAKE a tmux session a camp
    workspace session: the key binding's `if-shell` guard dispatches on
    `@camp_workspace`, and `window-dispatch` reads `@camp_group`/`@camp_slug`
    back to decide which workspace it is composing into. A session missing
    any of them is a session the binding will never fire for.

    So a create whose marking tmux refused did not create a workspace
    session, and must not answer CREATED. It answers FAILED carrying tmux's
    own words, and the half-made session is killed rather than left behind —
    otherwise the NEXT create at the same name answers ALREADY_EXISTED off
    its `has_session` probe and hands the operator that permanently unmarked
    session instead, with no path back.

    Driven once per option so no single one can regress unnoticed, and the
    refusal is varied across tmux's two failure shapes (a non-zero exit, and
    `None` for "could not be asked at all").
    """
    from camp.launch.workspace_session import (
        WorkspaceSessionOutcome,
        create_workspace_session,
    )

    for key in ("@camp_workspace", "@camp_group", "@camp_slug"):
        for answer in ("non-zero", None):
            tmux = _FakeTmux(failing_option=key, failing_option_answer=answer)
            ws = tmp_path / "ws"
            ws.mkdir(exist_ok=True)

            result = create_workspace_session(
                "testgroup", "slug", ws, env={"HOME": str(tmp_path)}, tmux=tmux
            )

            assert result.outcome is WorkspaceSessionOutcome.FAILED, (key, answer)
            assert key in (result.error or ""), (key, answer, result.error)
            assert tmux.killed == [result.session_name], (key, answer)
            assert tmux.install_binding_calls == [], (key, answer)


def test_a_fully_marked_session_is_created_and_never_killed(tmp_path):
    """The other side of the branch above: when every option lands, the
    session survives and the binding is installed — so the refusal path is
    reached by the marking answer, not by the code path always running."""
    from camp.launch.workspace_session import (
        WorkspaceSessionOutcome,
        create_workspace_session,
    )

    tmux = _FakeTmux()
    ws = tmp_path / "ws"
    ws.mkdir()

    result = create_workspace_session(
        "testgroup", "slug", ws, env={"HOME": str(tmp_path)}, tmux=tmux
    )

    assert result.outcome is WorkspaceSessionOutcome.CREATED
    assert tmux.killed == []
    assert len(tmux.install_binding_calls) == 1


class TestConnectArmReconciliation:
    """`create_or_connect_workspace_session` reconciles the window record
    to tmux on the connect arm — after `has_session_with_reason` answers
    present, before the `DoorProbe` is returned — and never on the create
    arm, per task/the-door-reconciles-on-connect-and-the-listing-never-
    writes."""

    def test_connect_arm_drops_a_window_closed_in_tmux_and_carries_the_change(self, tmp_path):
        from camp.group.window_record import WindowEntry, window_record_path_for, write_window_record
        from camp.launch.tmux import WindowListing
        from camp.launch.window_reconcile import Dropped, Reconciled
        from camp.launch.workspace_session import (
            DoorState,
            create_or_connect_workspace_session,
        )

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        surviving = WindowEntry(window_id="@1", name="planning", cwd="repo", conversation_id="c1")
        closed = WindowEntry(window_id="@3", name="review", cwd="repo", conversation_id="41aa")
        write_window_record(window_record_path_for(ws_dir), [surviving, closed])

        tmux = _FakeDoorTmux(
            present=True,
            list_windows_answer=WindowListing(
                windows=(_window("@1", "planning"),), dropped=0
            ),
        )

        probe = create_or_connect_workspace_session(
            "trailhead", "camp-cli", ws_dir, env={"HOME": str(tmp_path)}, tmux=tmux
        )

        assert probe.state is DoorState.CONNECTED
        assert isinstance(probe.reconcile_outcome, Reconciled)
        assert probe.reconcile_outcome.changes == (
            Dropped(window_id="@3", name="review", conversation_id="41aa"),
        )
        from camp.group.window_record import read_window_record

        reread = read_window_record(window_record_path_for(ws_dir))
        assert [e.window_id for e in reread.entries] == ["@1"]

    def test_connect_arm_with_a_matching_record_leaves_the_file_byte_for_byte_unchanged(
        self, tmp_path
    ):
        from camp.group.window_record import WindowEntry, window_record_path_for, write_window_record
        from camp.launch.tmux import WindowListing
        from camp.launch.window_reconcile import Reconciled
        from camp.launch.workspace_session import (
            DoorState,
            create_or_connect_workspace_session,
        )

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        entry = WindowEntry(window_id="@1", name="planning", cwd="repo", conversation_id="c1")
        path = window_record_path_for(ws_dir)
        write_window_record(path, [entry])
        before_bytes = path.read_bytes()
        before_mtime_ns = path.stat().st_mtime_ns

        tmux = _FakeDoorTmux(
            present=True,
            list_windows_answer=WindowListing(
                windows=(_window("@1", "planning"),), dropped=0
            ),
        )

        probe = create_or_connect_workspace_session(
            "trailhead", "camp-cli", ws_dir, env={"HOME": str(tmp_path)}, tmux=tmux
        )

        assert probe.state is DoorState.CONNECTED
        assert isinstance(probe.reconcile_outcome, Reconciled)
        assert probe.reconcile_outcome.changes == ()
        assert path.read_bytes() == before_bytes
        assert path.stat().st_mtime_ns == before_mtime_ns

    def test_connect_arm_with_a_corrupt_record_yields_not_reconciled_and_leaves_the_file_untouched(
        self, tmp_path
    ):
        from camp.group.window_record import window_record_path_for
        from camp.launch.tmux import WindowListing
        from camp.launch.window_reconcile import NotReconciled
        from camp.launch.workspace_session import (
            DoorState,
            create_or_connect_workspace_session,
        )

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        path = window_record_path_for(ws_dir)
        path.write_text("not json", encoding="utf-8")
        before = path.read_bytes()

        tmux = _FakeDoorTmux(present=True, list_windows_answer=WindowListing(windows=(), dropped=0))

        probe = create_or_connect_workspace_session(
            "trailhead", "camp-cli", ws_dir, env={"HOME": str(tmp_path)}, tmux=tmux
        )

        assert probe.state is DoorState.CONNECTED
        assert isinstance(probe.reconcile_outcome, NotReconciled)
        assert str(path) in probe.reconcile_outcome.reason
        assert path.read_bytes() == before

    def test_connect_arm_with_an_unanswered_listing_yields_not_reconciled_with_no_write(
        self, tmp_path
    ):
        from camp.group.window_record import WindowEntry, window_record_path_for, write_window_record
        from camp.launch.tmux import UNANSWERED
        from camp.launch.window_reconcile import NotReconciled
        from camp.launch.workspace_session import (
            DoorState,
            create_or_connect_workspace_session,
        )

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        entry = WindowEntry(window_id="@1", name="planning", cwd="repo", conversation_id="c1")
        path = window_record_path_for(ws_dir)
        write_window_record(path, [entry])
        before = path.read_bytes()

        tmux = _FakeDoorTmux(present=True, list_windows_answer=UNANSWERED)

        probe = create_or_connect_workspace_session(
            "trailhead", "camp-cli", ws_dir, env={"HOME": str(tmp_path)}, tmux=tmux
        )

        assert probe.state is DoorState.CONNECTED
        assert isinstance(probe.reconcile_outcome, NotReconciled)
        assert path.read_bytes() == before

    def test_create_arm_never_calls_list_windows(self, tmp_path):
        from camp.launch.workspace_session import (
            DoorState,
            create_or_connect_workspace_session,
        )

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        tmux = _FakeDoorTmux(present=False, list_windows_raises=True)

        probe = create_or_connect_workspace_session(
            "trailhead", "camp-cli", ws_dir, env={"HOME": str(tmp_path)}, tmux=tmux
        )

        assert probe.state is DoorState.CREATED
        assert probe.reconcile_outcome is None
        assert tmux.list_windows_calls == []


def _window(window_id, name, current_path="/ws", current_command="bash"):
    from camp.launch.tmux import TmuxWindow

    return TmuxWindow(
        window_id=window_id, current_path=current_path, current_command=current_command, name=name
    )
