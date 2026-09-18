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
    """A tmux stand-in exposing exactly the three calls the door dispatch
    and `create_workspace_session` issue: `has_session`
    (`has_session_with_reason`), `new_session`, and `switch_client`.
    Anything else the door path must never reach (`list_sessions`, etc.) is
    deliberately absent, so a call that reaches it fails loudly with
    `AttributeError` rather than degrading silently.

    `present` is the FIRST `has_session` answer; `reprobe` is every answer
    after the first (the unrecognised-create-failure re-probe).
    `unanswered_reason` is what `has_session_with_reason` reports alongside
    a `None` `present` — tmux's own words for why it could not answer.
    `switch_client_unanswered` makes `switch_client` answer `None` (tmux
    could not be asked at all) instead of a `CompletedProcess`.
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
) -> Path:
    """Wires a resolvable `--group g` carrying exactly one workspace
    (`slug`), at a real, unique directory, and points every tmux call the
    door dispatch issues at `tmux` — the caller's own `_DoorTmux` (or
    equivalent) — via the same `stop_module.Tmux` factory seam
    `_attach_session_context` reads.
    """
    state = tmp_path / "state"
    ws = state / group_name / "worktrees" / slug
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
    the switch-client call's own, not swallowed into a generic failure."""
    _isolated_env(tmp_path, monkeypatch)
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
    tmux = _DoorTmux(present=False, switch_client_returncode=3)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux)
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    fake_stdout = _FakeTTY()
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    code = _run(["attach", "camp-cli", "--group", "g"], monkeypatch)

    assert code == 3
    assert "created" in fake_stdout.getvalue()
    assert tmux.switch_client_calls == [_derived_name("g", "camp-cli")]


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
# A slug naming no workspace falls through to the retired ref path
# ---------------------------------------------------------------------------


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
