"""Test contract: `camp attach` wired into the CLI, with `--host` and `-a`.

Every test drives the real entry point (`camp.cli.dispatch.main`), never an
internal function directly — the task this file covers is explicitly "each of
the six forms reaches its intended path, asserted through the CLI entry
point rather than by calling internals."

Test contract (from
`task/wire-camp-attach-into-the-cli-with-host-and-all-hosts`):

- The verb answers from a directory belonging to no group, like `kill` and
  unlike `sessions`.
- Each of the six forms reaches its intended path.
- `-a` with exactly one machine matching hands off to that machine; with two
  matching it refuses, exits 2, and names both; with none it reports no
  match naming every machine asked.
- `-a` where a declared machine did not answer refuses, names the silent
  machine, and names `--host` as the way through.
- The declared machines are probed concurrently: pinned with a
  `threading.Barrier`, never a wall-clock budget.
- A state-changing invocation is unaffected: `-a` on a mutating verb still
  refuses.
- No invocation that exists today changes shape, exit code, or cost.
- The README's documented `camp attach` forms parse and dispatch through
  the real entry point (`test_readme_conformance.py`).

No real tmux, ssh, or exec is ever touched: the tmux seam, the harness seam,
the SSH transport, and the exec handoff seam are all injected.
"""
from __future__ import annotations

import importlib
import json
import sys
import threading
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
    _NOW,
    _UUID_A,
    _FakeHarness as _BaseFakeHarness,
    _FakeTmux,
    _env,
    _group,
    _launched_pane,
    _record,
    _transcript,
    _workspace,
)


class _Harness(_BaseFakeHarness):
    """`_FakeHarness` plus the one extra method `_session_pool` needs."""

    def __init__(self, transcripts: list) -> None:
        self._transcripts = transcripts

    def session_transcripts(self, workspace=None, *, env=None):
        return self._transcripts


def _dispatch_module():
    return importlib.import_module("camp.cli.dispatch")


def _cli_session_module():
    return importlib.import_module("camp.cli.session")


def _launch_session_module():
    return importlib.import_module("camp.launch.session")


def _launch_stop_module():
    return importlib.import_module("camp.launch.stop")


def _host_config_module():
    return importlib.import_module("camp.host.config")


def _host_transport_module():
    return importlib.import_module("camp.host.transport")


def _host_handoff_module():
    return importlib.import_module("camp.host.handoff")


def _wire_local_session(monkeypatch, *, tmp_path: Path, live: bool = True):
    """One live, camp-owned session `_session_pool`/`local_pool` can find,
    without touching any real group config, tmux, or harness."""
    state = tmp_path / "state"
    ws = _workspace(state, "g", "feat-a")
    env = _env(state)
    harness = _Harness([_transcript(_UUID_A, ws)])
    derived = f"camp-feat-a-{_UUID_A[:8]}"
    tmux = _FakeTmux({derived: _launched_pane(harness, _UUID_A, derived, ws)})

    cli_session = _cli_session_module()
    launch_session = _launch_stop = _launch_session_module()
    stop_module = _launch_stop_module()

    monkeypatch.setattr(cli_session, "_addressable_harnesses", lambda groups, **k: [harness])
    monkeypatch.setattr(cli_session, "_parsable_groups", lambda: [_group("g")])
    monkeypatch.setattr(
        launch_session,
        "enumerate_records",
        lambda h, ws_, env_: [_record(_UUID_A, ws)] if live else [],
    )
    monkeypatch.setattr(stop_module, "Tmux", lambda *a, **k: tmux)

    return env, derived, tmux


def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))


def _run(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    dispatch = _dispatch_module()
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    try:
        dispatch.main()
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


def _hosts_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *hosts: str) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    body = "".join(f"[hosts.{name}]\n" for name in hosts)
    (cfg / "hosts.toml").write_text(body, encoding="utf-8")
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))


# ---------------------------------------------------------------------------
# Contract item 1 — groupless, like `camp kill`
# ---------------------------------------------------------------------------


def test_attach_answers_from_a_directory_belonging_to_no_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    code = _run(["attach", "no-such-ref"], monkeypatch)
    err = capsys.readouterr().err
    assert code != 0
    assert err.startswith("camp attach:"), err
    assert "no group resolved" not in err and "no camp group" not in err, err


# ---------------------------------------------------------------------------
# Contract item 2 — each of the six forms reaches its intended path
# ---------------------------------------------------------------------------


def test_bare_picker_reaches_the_local_picker_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """No tty in a test process, so the picker's own terminal-guard refusal
    is the distinguishing signal that THIS path (not ref resolution, not the
    cross-host forms) was reached."""
    _isolated_env(tmp_path, monkeypatch)
    _wire_local_session(monkeypatch, tmp_path=tmp_path)

    code = _run(["attach"], monkeypatch)

    err = capsys.readouterr().err
    assert code != 0
    assert "no terminal to prompt into" in err, err


def test_ref_form_reaches_local_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    _wire_local_session(monkeypatch, tmp_path=tmp_path)

    code = _run(["attach", "no-such-ref-at-all"], monkeypatch)

    err = capsys.readouterr().err
    assert code != 0
    assert "no session on this machine matches" in err, err


def test_resolve_json_form_reaches_the_machine_readable_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    _wire_local_session(monkeypatch, tmp_path=tmp_path)

    code = _run(["attach", "no-such-ref", "--resolve", "--json"], monkeypatch)

    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out == {"ok": False, "state": "no_match"}


def test_host_form_reaches_the_untouched_pass_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    handoff = _host_handoff_module()
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))

    code = _run(["attach", "myref", "--host", "andromeda"], monkeypatch)

    assert code == 0
    assert len(seen) == 1
    argv = seen[0]
    assert argv[0] == "ssh"
    assert argv[-1].endswith("attach myref") or "attach myref" in argv[-1]


def test_ref_all_hosts_form_reaches_the_cross_host_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    _wire_local_session(monkeypatch, tmp_path=tmp_path)
    transport = _host_transport_module()

    calls: list = []

    def fake_run_camp(host, remote_argv, **kw):
        calls.append(remote_argv)
        return transport.Answered(stdout=json.dumps({"ok": False}), stderr="", exit_code=0)

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(["attach", "no-such-ref", "-a"], monkeypatch)

    assert code != 0
    assert calls == [["attach", "no-such-ref", "--resolve", "--json"]]


def test_bare_all_hosts_form_reaches_the_cross_host_picker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    _wire_local_session(monkeypatch, tmp_path=tmp_path)
    transport = _host_transport_module()

    calls: list = []

    def fake_run_camp(host, remote_argv, **kw):
        calls.append(remote_argv)
        return transport.Answered(stdout=json.dumps({"ok": True, "rows": []}), stderr="", exit_code=0)

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    _run(["attach", "-a"], monkeypatch)

    # Distinguishes this path from every other one of the six: only the bare
    # cross-host picker probes with `--list --json`.
    assert calls == [["attach", "--list", "--json"]]


# ---------------------------------------------------------------------------
# Contract item 3 — 0 / 1 / many matches under `-a`
# ---------------------------------------------------------------------------


def test_dash_a_with_exactly_one_remote_match_hands_off_to_that_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    _wire_local_session(monkeypatch, tmp_path=tmp_path)  # local: no match for this ref
    transport = _host_transport_module()
    handoff = _host_handoff_module()

    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.Answered(
            stdout=json.dumps({"ok": True, "session_id": "s1"}), stderr="", exit_code=0
        ),
    )
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))

    code = _run(["attach", "not-the-local-one", "-a"], monkeypatch)

    assert code == 0
    assert len(seen) == 1
    assert "attach not-the-local-one" in seen[0][-1]


def test_dash_a_with_two_machines_matching_refuses_and_names_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _hosts_env(tmp_path, monkeypatch, "andromeda", "vega")
    _wire_local_session(monkeypatch, tmp_path=tmp_path)
    transport = _host_transport_module()
    handoff = _host_handoff_module()

    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.Answered(
            stdout=json.dumps({"ok": True}), stderr="", exit_code=0
        ),
    )
    # Never a real exec — a mutation that wrongly treats this as one match
    # must not be able to escape into a real `os.execvp("ssh", …)`.
    monkeypatch.setattr(
        handoff, "handoff", lambda argv: (_ for _ in ()).throw(AssertionError(
            "must refuse on ambiguity, never hand off"
        ))
    )

    code = _run(["attach", "not-the-local-one", "-a"], monkeypatch)

    err = capsys.readouterr().err
    assert code == 2
    assert "andromeda" in err and "vega" in err


def test_dash_a_with_no_matches_names_every_machine_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    _wire_local_session(monkeypatch, tmp_path=tmp_path)
    transport = _host_transport_module()
    handoff = _host_handoff_module()

    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.Answered(
            stdout=json.dumps({"ok": False}), stderr="", exit_code=0
        ),
    )
    monkeypatch.setattr(
        handoff, "handoff", lambda argv: (_ for _ in ()).throw(AssertionError(
            "must refuse on no match, never hand off"
        ))
    )

    code = _run(["attach", "no-match-anywhere", "-a"], monkeypatch)

    err = capsys.readouterr().err
    assert code != 0
    assert "no session on any declared machine matches" in err
    assert "andromeda" in err


# ---------------------------------------------------------------------------
# Contract item 4 — a silent declared machine refuses, never guesses
# ---------------------------------------------------------------------------


def test_dash_a_refuses_when_a_declared_machine_does_not_answer_even_with_a_match_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _hosts_env(tmp_path, monkeypatch, "andromeda", "silent-host")
    _wire_local_session(monkeypatch, tmp_path=tmp_path)
    transport = _host_transport_module()
    handoff = _host_handoff_module()

    def fake_run_camp(host, remote_argv, **kw):
        # host.ssh distinguishes which declared host this call is for.
        if host.ssh == "andromeda":
            return transport.Answered(
                stdout=json.dumps({"ok": True, "session_id": "s1"}), stderr="", exit_code=0
            )
        return transport.Unreachable(reason="Connection timed out")

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)
    # Must NOT attach on the strength of the machine that did answer — a real
    # handoff call here is exactly the defect this test exists to catch.
    monkeypatch.setattr(
        handoff, "handoff", lambda argv: (_ for _ in ()).throw(AssertionError(
            "must refuse when a declared machine is silent, never hand off"
        ))
    )

    code = _run(["attach", "matches-on-andromeda", "-a"], monkeypatch)

    err = capsys.readouterr().err
    assert code != 0
    assert "silent-host" in err
    assert "did not answer" in err
    assert "--host" in err


# ---------------------------------------------------------------------------
# Contract item 5 — declared machines probed concurrently (barrier, not clock)
# ---------------------------------------------------------------------------


def test_declared_machines_are_probed_concurrently_via_barrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A serial fan-out calls one host's `run_camp` at a time, so fewer than
    N threads ever reach the barrier together and `.wait()` raises
    `BrokenBarrierError` for every host, in turn. A concurrent fan-out starts
    every worker at once and the barrier releases immediately for all of
    them — asserted structurally (every host answers `ok: True`), never by
    wall clock (see lesson
    a-wall-clock-budget-assertion-measures-host-load-as-much-as-code).
    """
    n = 4
    host_names = [f"host{i}" for i in range(n)]
    _hosts_env(tmp_path, monkeypatch, *host_names)
    _wire_local_session(monkeypatch, tmp_path=tmp_path)
    transport = _host_transport_module()

    barrier = threading.Barrier(n, timeout=5)

    def fake_run_camp(host, remote_argv, **kw):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            return transport.Unreachable(reason="barrier never released — not concurrent")
        return transport.Answered(
            stdout=json.dumps({"ok": True, "session_id": "s1"}), stderr="", exit_code=0
        )

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(["attach", "matches-everywhere", "-a"], monkeypatch)

    err = capsys.readouterr().err
    # If the fan-out were serial, at least one host's barrier.wait() would
    # break and that host would be reported silent — this assertion is what
    # a regression to a serial loop actually breaks.
    assert "barrier never released" not in err
    assert "did not answer" not in err
    # Every host matched, so this refuses on ambiguity (>1 machine), not on
    # silence — proving all four genuinely answered.
    assert code == 2
    for name in host_names:
        assert name in err


# ---------------------------------------------------------------------------
# Contract item 6 — a mutating verb's `-a` is unaffected
# ---------------------------------------------------------------------------


def test_dash_a_on_a_mutating_verb_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)

    code = _run(["kill", "-a", "some-ref"], monkeypatch)

    err = capsys.readouterr().err
    assert code != 0
    assert "--all-hosts has no meaning here" in err


# ---------------------------------------------------------------------------
# Contract item 7 — no existing invocation changes shape, exit code, or cost
# ---------------------------------------------------------------------------


def test_list_host_and_all_hosts_are_unaffected_by_attachs_addition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    transport = _host_transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.Answered(stdout="[]", stderr="", exit_code=0),
    )

    code = _run(["list", "--host", "andromeda", "--json"], monkeypatch)
    out = capsys.readouterr().out.strip()

    assert code == 0
    assert out == "[]"


def test_a_mistyped_attach_only_flag_is_not_silently_accepted_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Negative control: `--resolve` has meaning only for `camp attach`. A
    verb this dispatcher has never heard of it for must not silently start
    behaving differently because attach now exists."""
    _isolated_env(tmp_path, monkeypatch)
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))

    _run(["status", "--resolve"], monkeypatch)

    err = capsys.readouterr().err
    assert "camp attach:" not in err
