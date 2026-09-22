"""Test contract: `camp attach [<slug>]` dispatches the door — create or
connect, then hand the terminal over — end to end through the real entry
point (`camp.cli.dispatch.main`), never by calling `_open_workspace_door`
directly.

Test contract (from
`task/camp-attach-opens-the-door-and-hands-over-the-terminal`, reshaped
2026-09-17 once the handover unknown resolved):

- `camp attach <slug> --resolve --json` and `camp attach --list --json`
  issue no create call and never reach the exec seam — the regression that
  must be red before the gate exists.
- Outside tmux, a successful invocation reaches the exec seam exactly once
  with `["tmux", "attach-session", "-t", "=<derived name>"]`.
- Inside tmux, the same invocation reaches the `Tmux` seam with a
  `switch-client` targeting the derived name, and never reaches the exec
  seam at all.
- The two arms are distinguishable by seam, not only by verb.
- The probe answering present yields `connected` with no create; answering
  absent yields `created` with exactly one create.
- A create failing with `duplicate session: <name>` yields `connected`, exit
  0, no second create.
- A create failing with an unrecognised stderr, where a re-probe then finds
  the session present, also yields `connected` (locale-robustness path).
- A create failing with an unrecognised stderr where the re-probe finds
  nothing yields the refusal, exit 1, tmux's stderr in the message, and the
  exec seam is never reached.
- The probe answering unanswered (`None`) yields the refusal, exit 1, no
  create, no handoff, and names `camp list`. The no-server stderr shape
  (answered `False`) yields a create instead.
- The exec seam raising `OSError` on the success path, after a create
  succeeded, produces the outcome line and then camp's own `camp: <message>`
  refusal and exit 1.
- Without a terminal, a successful invocation creates the session, prints
  the report, exits 0, and never reaches the exec seam.
- `--json` on created, connected, and each refusal emits an object, with
  `attached` true only on the two handover arms.
- A slug naming no workspace still falls through to the retired ref path.

No real tmux, ssh, or exec is ever touched: the tmux seam and the exec
handoff seam are both injected fakes.
"""
from __future__ import annotations

import importlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_TESTS_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_launch_stop import (  # noqa: E402
    _UUID_A,
    _FakeHarness as _BaseFakeHarness,
    _group,
    _transcript,
)


class _Harness(_BaseFakeHarness):
    """`_FakeHarness` plus the one extra method `_session_pool` needs."""

    def __init__(self, transcripts: list) -> None:
        self._transcripts = transcripts

    def session_transcripts(self, workspace=None, *, env=None):
        return self._transcripts


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class _DoorTmux:
    """A tmux stand-in exposing the calls the door dispatch and
    `create_workspace_session` issue: `has_session`
    (`has_session_with_reason`), `new_session`, `switch_client`, and —
    since `create_workspace_session` now marks a CREATED session and
    installs the window-creation-key binding — `set_option` and
    `list_window_binding`/`install_window_binding`. Anything else the door
    path must never reach (`list_sessions`, etc.) is deliberately absent,
    so a call that reaches it fails loudly with `AttributeError` rather
    than degrading silently.

    `present` is the FIRST `has_session` answer; `reprobe` is every answer
    after the first (the unrecognised-create-failure re-probe).
    `unanswered_reason` is what `has_session_with_reason` reports alongside
    a `None` `present` — tmux's own words for why it could not answer.
    `switch_client_unanswered` makes `switch_client` answer `None` (tmux
    could not be asked at all) instead of a `CompletedProcess`.

    `list_windows_answer` is what the connect arm's reconciliation call
    gets back — an empty, answered listing by default, so a test that does
    not care about reconciliation is unaffected by the connect arm now
    reading the window record against tmux.
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

    def has_session(self, name: str) -> bool | None:
        self.has_session_calls.append(name)
        return self._present if len(self.has_session_calls) == 1 else self._reprobe

    def has_session_with_reason(self, name: str) -> tuple[bool | None, str | None]:
        present = self.has_session(name)
        return present, (self._unanswered_reason if present is None else None)

    def list_windows(self, name: str):
        self.list_windows_calls.append(name)
        if self._list_windows_answer is not None:
            return self._list_windows_answer
        from camp.launch.tmux import WindowListing

        return WindowListing(windows=(), dropped=0)

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


class _RaisingNewSessionTmux:
    """A tmux double for the read-only-probe regression: fails the test the
    instant `new_session` is called at all, rather than answering it —
    the probes this covers (`--resolve --json`, `--list --json`) must
    never reach the door's create path in the first place.
    """

    def has_session(self, name: str) -> bool:
        return True

    def new_session(self, *a, **k):
        raise AssertionError(
            "a read-only probe must never reach the door's create call"
        )

    def list_sessions(self):
        from camp.launch.stop import SessionListing

        return SessionListing(sessions=())


def _dispatch_module():
    return importlib.import_module("camp.cli.dispatch")


def _cli_session_module():
    return importlib.import_module("camp.cli.session")


def _launch_session_module():
    return importlib.import_module("camp.launch.session")


def _launch_stop_module():
    return importlib.import_module("camp.launch.stop")


def _host_handoff_module():
    return importlib.import_module("camp.host.handoff")


def _lifecycle_module():
    return importlib.import_module("camp.provision.lifecycle")


def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _run(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    dispatch = _dispatch_module()
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    try:
        dispatch.main()
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


def _wire_one_workspace(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tmp_path: Path,
    tmux,
    slug: str = "camp-cli",
    group_name: str = "g",
    ws_dir: Path | None = None,
) -> Path:
    """Wires a resolvable `--group g` carrying exactly one workspace
    (`slug`), at a real, unique directory, and points every tmux call the
    door dispatch issues at `tmux` — the caller's own `_DoorTmux` (or
    equivalent) — via the same `stop_module.Tmux` factory seam
    `_attach_session_context` reads. *ws_dir* overrides the default
    location under `tmp_path/state` — used to land the workspace at, under,
    or above a credential store."""
    ws = ws_dir if ws_dir is not None else (tmp_path / "state" / group_name / "worktrees" / slug)
    ws.mkdir(parents=True, exist_ok=True)
    harness = _Harness([_transcript(_UUID_A, ws)])

    cli_session = _cli_session_module()
    launch_session = _launch_session_module()
    stop_module = _launch_stop_module()
    lifecycle = _lifecycle_module()

    monkeypatch.setattr(cli_session, "_addressable_harnesses", lambda groups, **k: [harness])
    monkeypatch.setattr(cli_session, "_parsable_groups", lambda: [_group(group_name)])
    monkeypatch.setattr(launch_session, "enumerate_records", lambda h, ws_, env_: [])
    monkeypatch.setattr(stop_module, "Tmux", lambda *a, **k: tmux)

    def fake_cmd_ls_group(group, *, env=None, tmux=None, **kw):
        return lifecycle.GroupListing(
            entries=[
                {
                    "slug": slug,
                    "workspace_path": str(ws),
                    "state": None,
                    "window_count": None,
                }
            ],
            unmanaged=[],
            unmanaged_count=0,
            notice=None,
        )

    monkeypatch.setattr(lifecycle, "cmd_ls_group", fake_cmd_ls_group)
    return ws


def _derived_name(group_name: str, slug: str) -> str:
    from camp.launch.naming import workspace_session_name

    return workspace_session_name(group_name, slug)


# ---------------------------------------------------------------------------
# Read-only probes never reach the door's create path (regression)
# ---------------------------------------------------------------------------


def test_resolve_json_probe_never_creates_a_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    tmux = _RaisingNewSessionTmux()
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)

    code = _run(["attach", "camp-cli", "--resolve", "--json", "--group", "g"], monkeypatch)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    # The pre-existing `--resolve --json` answer shape (`_attach_resolve_
    # payload`'s `state`-keyed object) — never the door's own JSON shape
    # (`outcome`/`tmux_session`/`attached`), which would mean the probe
    # wrongly reached workspace-slug precedence instead of staying on the
    # retired ref-resolution path.
    assert payload == {"ok": False, "state": "no_match"}
    assert "outcome" not in payload


def test_list_json_probe_never_creates_a_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    tmux = _RaisingNewSessionTmux()
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)

    code = _run(["attach", "--list", "--json", "--group", "g"], monkeypatch)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True


# ---------------------------------------------------------------------------
# The two handover arms, distinguished by seam
# ---------------------------------------------------------------------------


def test_outside_tmux_reaches_the_exec_seam_with_attach_session_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    monkeypatch.delenv("TMUX", raising=False)
    tmux = _DoorTmux(present=False)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    handoff = _host_handoff_module()
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    monkeypatch.setattr(sys, "stdout", _FakeTTY())

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)

    assert code == 0
    derived = _derived_name("g", "camp-cli")
    assert seen == [["tmux", "attach-session", "-t", f"={derived}"]]
    assert tmux.switch_client_calls == []
    assert len(tmux.new_session_calls) == 1


def test_inside_tmux_reaches_the_switch_client_seam_never_the_exec_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
    tmux = _DoorTmux(present=False)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    handoff = _host_handoff_module()
    monkeypatch.setattr(
        handoff,
        "handoff",
        lambda argv: (_ for _ in ()).throw(
            AssertionError("the switch-client arm must never reach the exec seam")
        ),
    )
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    monkeypatch.setattr(sys, "stdout", _FakeTTY())

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)

    assert code == 0
    derived = _derived_name("g", "camp-cli")
    assert tmux.switch_client_calls == [derived]


# ---------------------------------------------------------------------------
# The switch-client handover arm's own failure — outcome already printed
# ---------------------------------------------------------------------------


def test_inside_tmux_switch_client_failure_exits_with_its_own_returncode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A handover can fail after the outcome line is already printed — the
    created/connected report must survive it, and the exit status must be
    the switch-client call's own, not swallowed into a generic failure.
    tmux's own stderr from the failed switch-client must reach the operator
    too — an operator inside tmux sees only this process's streams, so a
    silent nonzero exit here is the one arm that would explain nothing."""
    _isolated_env(tmp_path, monkeypatch)
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
    tmux = _DoorTmux(
        present=False,
        switch_client_returncode=3,
        switch_client_stderr="no current client\n",
    )
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    fake_stdout = _FakeTTY()
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)
    err = capsys.readouterr().err

    assert code == 3
    assert "created" in fake_stdout.getvalue()
    assert tmux.switch_client_calls == [_derived_name("g", "camp-cli")]
    assert "no current client" in err


def test_inside_tmux_switch_client_unanswered_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`switch_client` answering `None` (tmux could not be asked at all)
    must not be mistaken for a success (`returncode` `0`) — it is a
    failure with no exit code of its own to report, so it is exit 1."""
    _isolated_env(tmp_path, monkeypatch)
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
    tmux = _DoorTmux(present=False, switch_client_unanswered=True)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    fake_stdout = _FakeTTY()
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)

    assert code == 1
    assert "created" in fake_stdout.getvalue()
    assert tmux.switch_client_calls == [_derived_name("g", "camp-cli")]


# ---------------------------------------------------------------------------
# tmux's own stderr line reaches the TMUX_UNANSWERED refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "injected_reason",
    ["[Errno 2] No such file or directory: 'tmux'", "Command '['tmux', ...]' timed out after 5 seconds"],
    ids=["unlaunchable", "timeout"],
)
def test_tmux_unanswered_refusal_carries_tmuxs_own_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, injected_reason
) -> None:
    """Varies the injected reason across two distinct values and asserts
    each one reaches the refusal verbatim, alongside the existing `camp
    list` pointer — the test must depend on the input, not merely on the
    refusal arm being taken."""
    tmux = _DoorTmux(present=None, unanswered_reason=injected_reason)
    _isolated_env(tmp_path, monkeypatch)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)
    err = capsys.readouterr().err

    assert code == 1
    assert injected_reason in err, err
    assert "camp list" in err


def test_a_malformed_sibling_group_config_refuses_cleanly_never_tracebacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The credential-store gate's account union spans every group camp
    knows about, so it reads every group config — including one that has
    nothing to do with the attach target. A sibling group's unparseable
    config must surface as `camp attach`'s own refusal, never a raw
    traceback (the door is the one place `LaunchError` was previously
    uncaught on this path)."""
    _isolated_env(tmp_path, monkeypatch)
    groups_dir = tmp_path / "config" / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    (groups_dir / "broken.toml").write_text("not = [valid toml\n")
    tmux = _DoorTmux(present=False)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)
    err = capsys.readouterr().err

    assert code == 1
    assert "Traceback" not in err
    assert err.startswith("camp attach: ")
    assert tmux.switch_client_calls == []


def test_the_two_arms_are_distinguishable_by_seam_both_directions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Wiring both arms to one seam must fail this test: each direction
    below asserts the OTHER seam saw nothing, not only that its own arm
    fired."""
    _isolated_env(tmp_path, monkeypatch)

    monkeypatch.delenv("TMUX", raising=False)
    tmux_outside = _DoorTmux(present=False)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux_outside)
    handoff = _host_handoff_module()
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    monkeypatch.setattr(sys, "stdout", _FakeTTY())
    _run(["attach", "camp-cli", "--group", "g"], monkeypatch)
    capsys.readouterr()
    assert len(seen) == 1
    assert tmux_outside.switch_client_calls == []

    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
    tmux_inside = _DoorTmux(present=False)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux_inside)
    seen_exec: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen_exec.append(argv))
    _run(["attach", "camp-cli", "--group", "g"], monkeypatch)
    capsys.readouterr()
    assert seen_exec == []
    assert len(tmux_inside.switch_client_calls) == 1


# ---------------------------------------------------------------------------
# Create-vs-connect dispatch
# ---------------------------------------------------------------------------


def test_probe_present_yields_connected_with_no_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    tmux = _DoorTmux(present=True)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)

    code = _run(["attach", "camp-cli", "--group", "g", "--json"], monkeypatch)
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert out["outcome"] == "connected"
    assert tmux.new_session_calls == []


def test_probe_absent_yields_created_with_exactly_one_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    tmux = _DoorTmux(present=False)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)

    code = _run(["attach", "camp-cli", "--group", "g", "--json"], monkeypatch)
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert out["outcome"] == "created"
    assert len(tmux.new_session_calls) == 1
    assert out["tmux_session"] == _derived_name("g", "camp-cli"), (
        "the JSON object's tmux_session must be the actual derived name the "
        "dispatch created, not merely whatever render_json was handed"
    )


def test_duplicate_session_failure_yields_connected_no_second_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    derived = _derived_name("g", "camp-cli")
    tmux = _DoorTmux(
        present=False,
        create_returncode=1,
        create_stderr=f"duplicate session: {derived}\n",
    )
    _isolated_env(tmp_path, monkeypatch)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)

    code = _run(["attach", "camp-cli", "--group", "g", "--json"], monkeypatch)
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert out["outcome"] == "connected"
    assert len(tmux.new_session_calls) == 1, "no SECOND create attempt"


def test_unrecognised_failure_with_reprobe_present_yields_connected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The locale-robustness path: an unrecognised stderr does not trust
    the wording, it re-probes."""
    tmux = _DoorTmux(
        present=False,
        reprobe=True,
        create_returncode=1,
        create_stderr="sessions should be nested with care, unset TMUX to force\n",
    )
    _isolated_env(tmp_path, monkeypatch)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)

    code = _run(["attach", "camp-cli", "--group", "g", "--json"], monkeypatch)
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert out["outcome"] == "connected"
    assert tmux.has_session_calls.count(_derived_name("g", "camp-cli")) == 2


def test_unrecognised_failure_with_reprobe_absent_refuses_never_reaching_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    tmux = _DoorTmux(
        present=False,
        reprobe=False,
        create_returncode=1,
        create_stderr="error: unsafe socket directory\n",
    )
    _isolated_env(tmp_path, monkeypatch)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    handoff = _host_handoff_module()
    monkeypatch.setattr(
        handoff,
        "handoff",
        lambda argv: (_ for _ in ()).throw(
            AssertionError("a refused create must never reach the exec seam")
        ),
    )

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)
    err = capsys.readouterr().err

    assert code == 1
    assert "error: unsafe socket directory" in err
    assert _derived_name("g", "camp-cli") in err, (
        "the refusal must name the session it could not create, not tmux's "
        "stderr alone"
    )


@pytest.mark.parametrize(
    "injected_stderr",
    [
        "unsafe socket\nfake: forged a second line",
        "unsafe socket\rfake: overwrote the line",
    ],
    ids=["embedded-newline", "embedded-carriage-return"],
)
def test_refusal_stderr_carrying_control_characters_is_neutralized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, injected_stderr
) -> None:
    """`_refuse_door`'s plain-form line prints tmux's own stderr verbatim —
    hardened like the door's success line, so an embedded newline cannot
    forge a second stderr row and an embedded carriage return cannot
    rewrite it. Varied across two distinct control characters so the test
    depends on the input, not merely on the failure arm being taken."""
    tmux = _DoorTmux(
        present=False, reprobe=False, create_returncode=1, create_stderr=injected_stderr
    )
    _isolated_env(tmp_path, monkeypatch)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)
    err = capsys.readouterr().err

    assert code == 1
    lines = [line for line in err.split("\n") if line]
    refusal_lines = [line for line in lines if "unsafe socket" in line]
    assert len(refusal_lines) == 1, (
        f"an embedded control character must not forge a second refusal line: {err!r}"
    )
    refusal_line = refusal_lines[0]
    assert "\r" not in refusal_line, f"a raw carriage return reached the line: {refusal_line!r}"
    assert "forged a second line" in refusal_line or "overwrote the line" in refusal_line


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


def test_a_create_call_that_raises_refuses_with_its_own_message_never_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`camp attach` must never surface a raw traceback for a create call
    that times out or hits an unlaunchable tmux — it refuses, naming the
    session and carrying the exception's own message, same as any other
    create failure."""
    exc = subprocess.TimeoutExpired(cmd=["tmux", "new-session"], timeout=5)
    tmux = _RaisingCreateDoorTmux(present=False, exc=exc)
    _isolated_env(tmp_path, monkeypatch)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)
    err = capsys.readouterr().err

    assert code == 1
    assert str(exc) in err
    assert _derived_name("g", "camp-cli") in err
    assert len(tmux.new_session_calls) == 1


# ---------------------------------------------------------------------------
# Security audit Fix 2: a workspace under a credential store is a POLICY
# refusal, distinct from a transient tmux failure — in wording and in the
# `--json` object's machine-readable field, not merely in free text.
# ---------------------------------------------------------------------------


def test_a_credential_store_workspace_refuses_with_distinct_wording_and_json_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    home = tmp_path / "home"
    ws = home / ".ssh" / "state" / "g" / "worktrees" / "camp-cli"
    tmux = _DoorTmux(present=False)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux, ws_dir=ws)

    code = _run(["attach", "camp-cli", "--group", "g", "--json"], monkeypatch)
    out = json.loads(capsys.readouterr().out)

    assert code == 1
    assert out["ok"] is False
    assert out["outcome"] == "create_refused"
    assert tmux.new_session_calls == [], "the gate refuses before any create call reaches tmux"


def test_credential_refusal_and_transient_create_failure_emit_different_json_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Varies the failure KIND and asserts the `--json` `outcome` value
    changes with it — a consumer must be able to tell a policy refusal from
    a transient tmux failure without parsing `reason`'s free text."""
    _isolated_env(tmp_path, monkeypatch)
    home = tmp_path / "home"
    ws = home / ".ssh" / "state" / "g" / "worktrees" / "camp-cli"
    tmux_refused = _DoorTmux(present=False)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux_refused, ws_dir=ws)
    code = _run(["attach", "camp-cli", "--group", "g", "--json"], monkeypatch)
    assert code == 1
    refused_out = json.loads(capsys.readouterr().out)

    tmux_transient = _DoorTmux(
        present=False, reprobe=False, create_returncode=1, create_stderr="disk full\n"
    )
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux_transient)
    code = _run(["attach", "camp-cli", "--group", "g", "--json"], monkeypatch)
    assert code == 1
    transient_out = json.loads(capsys.readouterr().out)

    assert refused_out["outcome"] != transient_out["outcome"], (
        f"got the same outcome value for both: {refused_out['outcome']!r}"
    )
    assert transient_out["outcome"] == "create_failed"


# ---------------------------------------------------------------------------
# tmux unanswered vs. genuinely empty
# ---------------------------------------------------------------------------


def test_unanswered_probe_refuses_naming_camp_list_no_create_no_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    tmux = _DoorTmux(present=None)
    _isolated_env(tmp_path, monkeypatch)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    handoff = _host_handoff_module()
    monkeypatch.setattr(
        handoff,
        "handoff",
        lambda argv: (_ for _ in ()).throw(
            AssertionError("tmux unanswered must never reach the exec seam")
        ),
    )

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)
    err = capsys.readouterr().err

    assert code == 1
    assert "camp list" in err
    assert tmux.new_session_calls == []


def test_no_server_stderr_shape_yields_a_create_instead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`has_session` answering `False` (its own collapse of every non-zero
    exit, including the no-server shape) must reach create — distinct from
    `None` (unanswered), which must refuse instead. Collapsing the two is
    exactly `lesson/tmux-list-sessions-conflates-three-failure-causes-in-
    one-exit-status`."""
    tmux = _DoorTmux(present=False)
    _isolated_env(tmp_path, monkeypatch)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)

    code = _run(["attach", "camp-cli", "--group", "g", "--json"], monkeypatch)
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert out["outcome"] == "created"
    assert len(tmux.new_session_calls) == 1


# ---------------------------------------------------------------------------
# The exec seam raising after a successful create
# ---------------------------------------------------------------------------


def test_exec_oserror_after_create_prints_outcome_then_camps_own_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Drives the REAL `camp.host.handoff.handoff`, not a stand-in, so its
    own `OSError` catch runs — patches `os.execvp` itself (the only seam a
    test can reach without overriding `handoff`'s bound default
    parameter)."""
    tmux = _DoorTmux(present=False)
    _isolated_env(tmp_path, monkeypatch)
    monkeypatch.delenv("TMUX", raising=False)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    handoff = _host_handoff_module()
    monkeypatch.setattr(
        handoff.os, "execvp", lambda *a: (_ for _ in ()).throw(OSError("no such file"))
    )
    fake_stdout = _FakeTTY()
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)
    captured = capsys.readouterr()

    assert code == 1
    assert "created" in fake_stdout.getvalue()
    assert "camp: no such file" in captured.err
    assert len(tmux.new_session_calls) == 1


# ---------------------------------------------------------------------------
# No terminal — created/connected, reported, never handed over
# ---------------------------------------------------------------------------


def test_without_a_terminal_creates_and_reports_but_never_reaches_the_exec_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    tmux = _DoorTmux(present=False)
    _isolated_env(tmp_path, monkeypatch)
    monkeypatch.delenv("TMUX", raising=False)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    handoff = _host_handoff_module()
    monkeypatch.setattr(
        handoff,
        "handoff",
        lambda argv: (_ for _ in ()).throw(
            AssertionError("no terminal must never reach the exec seam")
        ),
    )

    code = _run(["attach", "camp-cli", "--group", "g", "--json"], monkeypatch)
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert out["outcome"] == "created"
    assert out["attached"] is False
    assert tmux.switch_client_calls == []


# ---------------------------------------------------------------------------
# --json shapes: created, connected, and refusals — attached only on the
# two handover arms
# ---------------------------------------------------------------------------


def test_json_created_and_connected_carry_attached_true_only_with_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    handoff = _host_handoff_module()
    monkeypatch.setattr(handoff, "handoff", lambda argv: None)

    tmux_created = _DoorTmux(present=False)
    _isolated_env(tmp_path, monkeypatch)
    monkeypatch.delenv("TMUX", raising=False)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux_created)
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    fake_stdout_created = _FakeTTY()
    monkeypatch.setattr(sys, "stdout", fake_stdout_created)
    _run(["attach", "camp-cli", "--group", "g", "--json"], monkeypatch)
    created_payload = json.loads(fake_stdout_created.getvalue())

    tmux_connected = _DoorTmux(present=True)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux_connected)
    fake_stdout_connected = _FakeTTY()
    monkeypatch.setattr(sys, "stdout", fake_stdout_connected)
    _run(["attach", "camp-cli", "--group", "g", "--json"], monkeypatch)
    connected_payload = json.loads(fake_stdout_connected.getvalue())

    assert created_payload["outcome"] == "created"
    assert created_payload["attached"] is True
    assert connected_payload["outcome"] == "connected"
    assert connected_payload["attached"] is True


def test_json_refusals_emit_an_ok_false_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    tmux = _DoorTmux(present=None)
    _isolated_env(tmp_path, monkeypatch)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)

    code = _run(["attach", "camp-cli", "--group", "g", "--json"], monkeypatch)
    out = json.loads(capsys.readouterr().out)

    assert code == 1
    assert out["ok"] is False
    assert "camp list" in out["reason"]


# ---------------------------------------------------------------------------
# Reconciliation on the connect arm — stderr carries the change lines (or
# the not-reconciled line) before the door's own outcome line; the door
# still connects and exits 0 either way; `--json`'s stdout object is
# unchanged in shape.
# task/the-door-reconciles-on-connect-and-the-listing-never-writes
# ---------------------------------------------------------------------------


def test_connected_with_a_dropped_window_prints_the_change_before_the_outcome_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.group.window_record import WindowEntry, window_record_path_for, write_window_record
    from camp.launch.tmux import WindowListing, TmuxWindow

    _isolated_env(tmp_path, monkeypatch)
    surviving = WindowEntry(window_id="@1", name="planning", cwd="repo", conversation_id="8f2c")
    closed = WindowEntry(window_id="@3", name="review", cwd="repo", conversation_id="41aa")
    tmux = _DoorTmux(
        present=True,
        list_windows_answer=WindowListing(
            windows=(TmuxWindow(window_id="@1", current_path="/repo", current_command="bash", name="planning"),),
            dropped=0,
        ),
    )
    ws = _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    write_window_record(window_record_path_for(ws), [surviving, closed])

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)
    captured = capsys.readouterr()

    assert code == 0
    err_lines = [line for line in captured.err.splitlines() if line]
    assert err_lines == [
        'camp: window record: dropped @3 "review" (closed in tmux; conversation 41aa)'
    ]
    assert captured.out.strip() == "connected camp-g-camp-cli"


def test_connected_with_a_matching_record_prints_nothing_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.group.window_record import WindowEntry, window_record_path_for, write_window_record
    from camp.launch.tmux import WindowListing, TmuxWindow

    _isolated_env(tmp_path, monkeypatch)
    entry = WindowEntry(window_id="@1", name="planning", cwd="repo", conversation_id="8f2c")
    tmux = _DoorTmux(
        present=True,
        list_windows_answer=WindowListing(
            windows=(TmuxWindow(window_id="@1", current_path="/repo", current_command="bash", name="planning"),),
            dropped=0,
        ),
    )
    ws = _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    write_window_record(window_record_path_for(ws), [entry])

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)
    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out.strip() == "connected camp-g-camp-cli"


def test_connected_with_a_corrupt_record_prints_a_not_reconciled_line_and_still_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.group.window_record import window_record_path_for

    _isolated_env(tmp_path, monkeypatch)
    tmux = _DoorTmux(present=True)
    ws = _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    path = window_record_path_for(ws)
    path.write_text("not json", encoding="utf-8")

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)
    captured = capsys.readouterr()

    assert code == 0
    err_lines = [line for line in captured.err.splitlines() if line]
    assert len(err_lines) == 1
    assert str(path) in err_lines[0]
    assert "not reconciled" in err_lines[0].lower()
    assert captured.out.strip() == "connected camp-g-camp-cli"
    assert path.read_text(encoding="utf-8") == "not json"


def test_connected_json_still_carries_the_change_lines_on_stderr_with_unchanged_json_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.group.window_record import WindowEntry, window_record_path_for, write_window_record
    from camp.launch.tmux import WindowListing, TmuxWindow

    _isolated_env(tmp_path, monkeypatch)
    surviving = WindowEntry(window_id="@1", name="planning", cwd="repo", conversation_id="8f2c")
    closed = WindowEntry(window_id="@3", name="review", cwd="repo", conversation_id="41aa")
    tmux = _DoorTmux(
        present=True,
        list_windows_answer=WindowListing(
            windows=(TmuxWindow(window_id="@1", current_path="/repo", current_command="bash", name="planning"),),
            dropped=0,
        ),
    )
    ws = _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    write_window_record(window_record_path_for(ws), [surviving, closed])

    code = _run(["attach", "camp-cli", "--group", "g", "--json"], monkeypatch)
    captured = capsys.readouterr()
    out = json.loads(captured.out)

    assert code == 0
    err_lines = [line for line in captured.err.splitlines() if line]
    assert err_lines == [
        'camp: window record: dropped @3 "review" (closed in tmux; conversation 41aa)'
    ]
    assert out == {
        "ok": True,
        "outcome": "connected",
        "slug": "camp-cli",
        "group": "g",
        "workspace_path": str(ws),
        "tmux_session": "camp-g-camp-cli",
        "attached": False,
    }


# ---------------------------------------------------------------------------
# A slug naming no workspace falls through to the retired ref path
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Resurrection — task/the-door-reads-the-record-resurrects-refuses-the-
# unreadable-and-reports-partial
# ---------------------------------------------------------------------------


def test_attach_with_a_partial_resurrection_prints_resurrected_line_and_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.group.window_record import WindowEntry, window_record_path_for, write_window_record
    from camp.launch.tmux import NewWindowResult

    _isolated_env(tmp_path, monkeypatch)
    tmux = _DoorTmux(
        present=False,
        first_window=NewWindowResult(window_id="@10", window_name="one"),
        window_answers=[None],
    )
    ws = _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    (ws / "one").mkdir()
    (ws / "two").mkdir()
    write_window_record(
        window_record_path_for(ws),
        [
            WindowEntry(window_id="@1", name="one", cwd="one", conversation_id="c1"),
            WindowEntry(window_id="@2", name="two", cwd="two", conversation_id="c2"),
        ],
    )

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)
    captured = capsys.readouterr()

    assert code == 2
    assert captured.out.strip() == "resurrected camp-g-camp-cli (1 of 2 windows; 1 did not come back)"
    assert "did not come back" in captured.err
    assert "c2" in captured.err


def test_attach_with_a_partial_resurrection_json_carries_the_windows_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.group.window_record import WindowEntry, window_record_path_for, write_window_record
    from camp.launch.tmux import NewWindowResult

    _isolated_env(tmp_path, monkeypatch)
    tmux = _DoorTmux(
        present=False,
        first_window=NewWindowResult(window_id="@10", window_name="one"),
        window_answers=[None],
    )
    ws = _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    (ws / "one").mkdir()
    (ws / "two").mkdir()
    write_window_record(
        window_record_path_for(ws),
        [
            WindowEntry(window_id="@1", name="one", cwd="one", conversation_id="c1"),
            WindowEntry(window_id="@2", name="two", cwd="two", conversation_id="c2"),
        ],
    )

    code = _run(["attach", "camp-cli", "--group", "g", "--json"], monkeypatch)
    out = json.loads(capsys.readouterr().out)

    assert code == 2
    assert out["outcome"] == "resurrected"
    assert out["windows"] == {"restored": 1, "failed": 1, "dropped": 0}


def test_attach_with_a_whole_resurrection_prints_the_plain_line_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.group.window_record import WindowEntry, window_record_path_for, write_window_record
    from camp.launch.tmux import NewWindowResult

    _isolated_env(tmp_path, monkeypatch)
    tmux = _DoorTmux(present=False, first_window=NewWindowResult(window_id="@10", window_name="one"))
    ws = _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    (ws / "one").mkdir()
    write_window_record(
        window_record_path_for(ws),
        [WindowEntry(window_id="@1", name="one", cwd="one", conversation_id="c1")],
    )

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out.strip() == "resurrected camp-g-camp-cli (1 windows)"


def test_attach_with_a_corrupt_record_refuses_naming_the_path_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.group.window_record import window_record_path_for

    _isolated_env(tmp_path, monkeypatch)
    tmux = _DoorTmux(present=False)
    ws = _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    path = window_record_path_for(ws)
    path.write_text("not json", encoding="utf-8")

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)
    err = capsys.readouterr().err

    assert code == 1
    assert "camp attach: " in err
    assert str(path) in err
    assert "could not be read" in err
    assert "refusing to resurrect" in err
    assert tmux.new_session_calls == []
    assert tmux.new_session_with_window_calls == []


def test_attach_with_a_corrupt_record_json_gives_the_refusal_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.group.window_record import window_record_path_for

    _isolated_env(tmp_path, monkeypatch)
    tmux = _DoorTmux(present=False)
    ws = _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    window_record_path_for(ws).write_text("not json", encoding="utf-8")

    code = _run(["attach", "camp-cli", "--group", "g", "--json"], monkeypatch)
    out = json.loads(capsys.readouterr().out)

    assert code == 1
    assert out == {"ok": False, "outcome": "record_unreadable", "reason": out["reason"]}
    assert str(window_record_path_for(ws)) in out["reason"]


def test_a_slug_naming_no_workspace_falls_through_to_the_retired_ref_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    tmux = _DoorTmux(present=False)
    _isolated_env(tmp_path, monkeypatch)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)

    code = _run(["attach", "no-such-slug-at-all", "--group", "g"], monkeypatch)
    err = capsys.readouterr().err

    assert code != 0
    assert tmux.new_session_calls == [], "a fall-through ref must never reach the door's create"
    assert "no session on this machine matches" in err, err
    assert "no-such-slug-at-all" in err, err
