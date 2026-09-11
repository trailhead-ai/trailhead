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
import io
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
    _UUID_B,
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


def _wire_local_ambiguous(monkeypatch, *, tmp_path: Path):
    """Two live, camp-owned sessions sharing a `camp-feat-` name prefix, so a
    ref of that prefix resolves ambiguously on this machine alone."""
    state = tmp_path / "state"
    ws_a = _workspace(state, "g", "feat-a")
    ws_b = _workspace(state, "g", "feat-b")
    env = _env(state)
    harness = _Harness([_transcript(_UUID_A, ws_a), _transcript(_UUID_B, ws_b)])
    derived_a = f"camp-feat-a-{_UUID_A[:8]}"
    derived_b = f"camp-feat-b-{_UUID_B[:8]}"
    tmux = _FakeTmux(
        {
            derived_a: _launched_pane(harness, _UUID_A, derived_a, ws_a),
            derived_b: _launched_pane(harness, _UUID_B, derived_b, ws_b),
        }
    )

    cli_session = _cli_session_module()
    launch_session = _launch_session_module()
    stop_module = _launch_stop_module()

    monkeypatch.setattr(cli_session, "_addressable_harnesses", lambda groups, **k: [harness])
    monkeypatch.setattr(cli_session, "_parsable_groups", lambda: [_group("g")])
    monkeypatch.setattr(
        launch_session,
        "enumerate_records",
        lambda h, ws_, env_: [_record(_UUID_A, ws_a), _record(_UUID_B, ws_b)],
    )
    monkeypatch.setattr(stop_module, "Tmux", lambda *a, **k: tmux)

    return env


def _wire_local_stopped_session(monkeypatch, *, tmp_path: Path):
    """One session the harness still transcribes but that is not live —
    resolves to `attach.resolve.NotRunning` on this machine."""
    state = tmp_path / "state"
    ws = _workspace(state, "g", "feat-a")
    env = _env(state)
    harness = _Harness([_transcript(_UUID_A, ws)])
    tmux = _FakeTmux({})

    cli_session = _cli_session_module()
    launch_session = _launch_session_module()
    stop_module = _launch_stop_module()

    monkeypatch.setattr(cli_session, "_addressable_harnesses", lambda groups, **k: [harness])
    monkeypatch.setattr(cli_session, "_parsable_groups", lambda: [_group("g")])
    monkeypatch.setattr(launch_session, "enumerate_records", lambda h, ws_, env_: [])
    monkeypatch.setattr(stop_module, "Tmux", lambda *a, **k: tmux)

    return env


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


def test_attach_reports_a_malformed_self_name_instead_of_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`self_host_name` raises `HostConfigError` on a malformed `self_name` —
    matched by every OTHER `--host`-aware verb's existing convention
    (`_dispatch_all_hosts_command`), never a bare traceback."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "hosts.toml").write_text('self_name = "Not-Valid!"\n', encoding="utf-8")
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))

    code = _run(["attach", "no-such-ref"], monkeypatch)

    err = capsys.readouterr().err
    assert code != 0
    assert err.startswith("camp attach:"), err
    assert "self_name" in err


def test_a_declared_host_named_the_same_as_self_name_is_a_refusal_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Minor (second review pass, dispatch.py:1145): nothing in
    `host/config.py` rejects a declared host sharing this machine's own
    `self_name` at config time. Left unguarded, the bare `-a` picker can
    route a `_RemoteAttachCandidate` row down the local handoff branch
    (`row.machine == self_name`) and raise `AttributeError: derived_name`
    when it is chosen — a camp refusal is owed instead.

    Drives the picker all the way to a selection (fake tty, remote row
    answered) rather than stopping at the "no terminal" refusal — otherwise
    this test would pass identically whether or not the guard exists, and
    would never actually reach the crash site it is pinning."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    cfg_text = 'self_name = "andromeda"\n\n[hosts.andromeda]\n'
    (cfg / "hosts.toml").write_text(cfg_text, encoding="utf-8")
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    _wire_local_session(monkeypatch, tmp_path=tmp_path)
    transport = _host_transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.Answered(
            stdout=json.dumps(
                {
                    "ok": True,
                    "rows": [
                        {
                            "session_id": "s-evil",
                            "derived_name": "camp-evil",
                            "group": "g2",
                            "slug": "evil",
                        }
                    ],
                }
            ),
            stderr="",
            exit_code=0,
        ),
    )
    monkeypatch.setattr(sys, "stdin", _FakeTTY("2\n"))
    monkeypatch.setattr(sys, "stdout", _FakeTTY())

    code = _run(["attach", "-a"], monkeypatch)

    err = capsys.readouterr().err
    assert code != 0
    assert err.startswith("camp attach:"), err
    assert "andromeda" in err


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


def test_bare_local_picker_handoff_uses_the_derived_name_not_the_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 2 (second review pass): `camp attach` (no ref, no `-a`), on a
    local pick, must exec `tmux attach -t <derived_name>` — never the
    harness's own `session_id`, which `local_argv`'s own docstring warns
    "addresses nothing in tmux". No prior test drove the local picker
    selection all the way to a captured handoff argv."""
    _isolated_env(tmp_path, monkeypatch)
    env, derived, _tmux = _wire_local_session(monkeypatch, tmp_path=tmp_path)
    handoff = _host_handoff_module()
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))
    monkeypatch.setattr(sys, "stdin", _FakeTTY("1\n"))
    monkeypatch.setattr(sys, "stdout", _FakeTTY())

    code = _run(["attach"], monkeypatch)

    assert code == 0
    assert seen == [["tmux", "attach", "-t", derived]]


def test_ref_form_reaches_local_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    _wire_local_session(monkeypatch, tmp_path=tmp_path)

    code = _run(["attach", "no-such-ref-at-all"], monkeypatch)

    err = capsys.readouterr().err
    assert code != 0
    # Finding 3 (second review pass): a populated pool with no match names
    # the pool THIS attach searched — never `camp sessions --recoverable`,
    # which lists exactly the stopped sessions attach refuses to touch.
    assert "no session on this machine matches" in err, err
    assert "camp sessions --recoverable" not in err, err


def test_ref_form_no_match_against_an_empty_pool_names_the_retention_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Finding 3 (second review pass): an empty pool is not a ref problem at
    all, and attach's own no-match wording says so in the harness's own
    retention terms — distinct from the populated-pool wording asserted
    above, the empty-vs-populated split `_die_unresolved` provides."""
    _isolated_env(tmp_path, monkeypatch)
    cli_session = _cli_session_module()
    launch_session = _launch_session_module()
    harness = _Harness([])

    monkeypatch.setattr(cli_session, "_addressable_harnesses", lambda groups, **k: [harness])
    monkeypatch.setattr(cli_session, "_parsable_groups", lambda: [_group("g")])
    monkeypatch.setattr(launch_session, "enumerate_records", lambda h, ws_, env_: [])

    code = _run(["attach", "no-such-ref"], monkeypatch)

    err = capsys.readouterr().err
    assert code != 0
    assert "reports no sessions at all" in err, err
    assert "no session on this machine matches" not in err, err


def test_ref_form_ambiguous_match_prints_candidates_and_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Finding 7: routed through `_die_unresolved`, an ambiguous local match
    still prints its candidates and exits `_AMBIGUOUS_EXIT_CODE`."""
    _isolated_env(tmp_path, monkeypatch)
    _wire_local_ambiguous(monkeypatch, tmp_path=tmp_path)

    code = _run(["attach", "camp-feat-"], monkeypatch)

    captured = capsys.readouterr()
    assert code == 2
    assert f"camp-feat-a-{_UUID_A[:8]}" in captured.out
    assert f"camp-feat-b-{_UUID_B[:8]}" in captured.out
    assert "2 sessions" in captured.err


def test_resolve_json_form_reaches_the_machine_readable_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    _wire_local_session(monkeypatch, tmp_path=tmp_path)

    code = _run(["attach", "no-such-ref", "--resolve", "--json"], monkeypatch)

    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out == {"ok": False, "state": "no_match"}


def test_ref_form_local_handoff_uses_the_derived_name_not_the_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 2 (second review pass): `camp attach <ref>` (no `-a`), on a
    successful local resolution, must exec `tmux attach -t <derived_name>` —
    never `session_id`. No prior test drove this success path to a captured
    handoff argv; the whole suite stayed green under the
    `derived_name` -> `session_id` mutation this test now catches."""
    _isolated_env(tmp_path, monkeypatch)
    env, derived, _tmux = _wire_local_session(monkeypatch, tmp_path=tmp_path)
    handoff = _host_handoff_module()
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))

    code = _run(["attach", _UUID_A[:8]], monkeypatch)

    assert code == 0
    assert seen == [["tmux", "attach", "-t", derived]]


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


def test_remote_attach_from_inside_a_local_multiplexer_warns_via_the_real_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC36, driven through `camp.cli.dispatch.main` rather than by hand
    assembling `warn_if_nested` + `remote_argv` — the key-prefix conflict
    warning is decided from the environment the operator is typing in, so it
    appears for a REMOTE attach launched from inside a LOCAL multiplexer."""
    from camp.attach import prefix_warning

    _hosts_env(tmp_path, monkeypatch, "andromeda")
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
    handoff = _host_handoff_module()
    monkeypatch.setattr(handoff, "handoff", lambda argv: None)

    code = _run(["attach", "myref", "--host", "andromeda"], monkeypatch)

    err = capsys.readouterr().err
    assert code == 0
    assert prefix_warning.MESSAGE in err


def test_ref_all_hosts_form_reaches_the_cross_host_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    _wire_local_session(monkeypatch, tmp_path=tmp_path)
    transport = _host_transport_module()
    handoff = _host_handoff_module()

    calls: list = []

    def fake_run_camp(host, remote_argv, **kw):
        calls.append(remote_argv)
        return transport.Answered(stdout=json.dumps({"ok": False}), stderr="", exit_code=0)

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)
    # Minor (second review pass): a live, camp-owned local session is wired
    # above with no ref match expected — a mutation that wrongly treats this
    # as a match must not be able to reach a real `os.execvp` in this
    # process.
    monkeypatch.setattr(
        handoff, "handoff", lambda argv: (_ for _ in ()).throw(AssertionError(
            "must refuse — this ref has no match anywhere, never hand off"
        ))
    )

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


def test_dash_a_ref_form_local_match_hands_off_using_the_derived_name_not_the_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 2 (second review pass, dispatch.py): `camp attach <ref> -a`
    with the sole match on THIS machine must exec `tmux attach -t
    <derived_name>` — never `session_id`. No declared host answers, so the
    only match is local."""
    _isolated_env(tmp_path, monkeypatch)
    env, derived, _tmux = _wire_local_session(monkeypatch, tmp_path=tmp_path)
    handoff = _host_handoff_module()
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))

    code = _run(["attach", _UUID_A[:8], "-a"], monkeypatch)

    assert code == 0
    assert seen == [["tmux", "attach", "-t", derived]]


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


def test_dash_a_with_a_local_ambiguity_plus_one_remote_match_refuses_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Finding 1 (second review pass): a ref that is locally ambiguous (two
    sessions on THIS machine) plus one remote machine answering `ok: true`
    must refuse exit 2 naming both — a local `Ambiguous` is itself evidence
    THIS machine matched, and must count toward the cross-machine tally
    rather than being read as "no local match" and silently handed to the
    remote machine (the previous fix's bug, reproduced here)."""
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    _wire_local_ambiguous(monkeypatch, tmp_path=tmp_path)
    transport = _host_transport_module()
    handoff = _host_handoff_module()

    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.Answered(
            stdout=json.dumps({"ok": True, "session_id": "s-remote"}), stderr="", exit_code=0
        ),
    )
    # A real exec here IS the defect this test exists to catch.
    monkeypatch.setattr(
        handoff, "handoff", lambda argv: (_ for _ in ()).throw(AssertionError(
            "must refuse — a local ambiguity plus a remote match is still an "
            "ambiguous reference, never hand off"
        ))
    )

    code = _run(["attach", "camp-feat-", "-a"], monkeypatch)

    err = capsys.readouterr().err
    assert code == 2
    assert "andromeda" in err
    assert "this machine" in err


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


def test_dash_a_ref_form_with_no_hosts_declared_still_reports_a_local_ambiguity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """With no hosts declared, `-a` must answer at least as well as the plain
    `camp attach <ref>` form over the identical local pool — a ref that is
    locally ambiguous is exit 2, never folded into the generic 'no machine
    matches' refusal (exit 1)."""
    _isolated_env(tmp_path, monkeypatch)
    _wire_local_ambiguous(monkeypatch, tmp_path=tmp_path)
    handoff = _host_handoff_module()
    monkeypatch.setattr(
        handoff, "handoff", lambda argv: (_ for _ in ()).throw(AssertionError(
            "must refuse on local ambiguity, never hand off"
        ))
    )

    code = _run(["attach", "camp-feat-", "-a"], monkeypatch)

    captured = capsys.readouterr()
    assert code == 2
    assert "matches" in captured.err and "2 sessions" in captured.err
    # Minor (second review pass, dispatch.py:1031): the `-a` local-ambiguity
    # refusal must list the candidates, like the plain form's
    # `_die_unresolved` path and the plan's own axiom that an ambiguous ref
    # "exits 2 and lists candidates" — not just a bare count.
    assert f"camp-feat-a-{_UUID_A[:8]}" in captured.out
    assert f"camp-feat-b-{_UUID_B[:8]}" in captured.out


def test_dash_a_ref_form_with_no_hosts_declared_still_reports_local_not_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A ref that resolves locally to a stopped session must refuse naming
    `camp launch --resume`, not the generic 'no machine matches' wording —
    the same distinction `camp attach <ref>` (no `-a`) already draws."""
    _isolated_env(tmp_path, monkeypatch)
    _wire_local_stopped_session(monkeypatch, tmp_path=tmp_path)
    handoff = _host_handoff_module()
    monkeypatch.setattr(
        handoff, "handoff", lambda argv: (_ for _ in ()).throw(AssertionError(
            "must refuse on a stopped local match, never hand off"
        ))
    )

    code = _run(["attach", _UUID_A[:8], "-a"], monkeypatch)

    err = capsys.readouterr().err
    assert code != 0
    assert "not running" in err
    assert "camp launch --resume" in err


def test_dash_a_ref_form_with_a_remote_not_running_state_hands_off_rather_than_a_generic_no_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Minor (second review pass, dispatch.py:1043): a remote host's
    `not_running` state is a match (a session THAT machine holds, just
    stopped) — never the generic 'no session on any declared machine
    matches'. `--host`'s own posture applies: the far side resolves and
    refuses in its own words (its own `camp launch --resume` hint), so the
    local probe hands off rather than fabricating a message about a machine
    it never fully resolved."""
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    _wire_local_session(monkeypatch, tmp_path=tmp_path)  # local: no match for this ref
    transport = _host_transport_module()
    handoff = _host_handoff_module()

    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.Answered(
            stdout=json.dumps(
                {"ok": False, "state": "not_running", "session_id": "s-stopped"}
            ),
            stderr="",
            exit_code=0,
        ),
    )
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))

    code = _run(["attach", "not-the-local-one", "-a"], monkeypatch)

    assert code == 0
    assert len(seen) == 1
    assert "attach not-the-local-one" in seen[0][-1]


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


def test_ref_all_hosts_probe_survives_a_raising_transport_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A raise out of `run_camp` itself (e.g. `ssh` missing locally —
    `FileNotFoundError` from `Popen`) must surface as camp's own silent-host
    refusal, never a traceback out of `pool.map`."""
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    _wire_local_session(monkeypatch, tmp_path=tmp_path)
    transport = _host_transport_module()

    def raising_run_camp(host, remote_argv, **kw):
        raise FileNotFoundError("ssh: not found")

    monkeypatch.setattr(transport, "run_camp", raising_run_camp)

    code = _run(["attach", "not-the-local-one", "-a"], monkeypatch)

    err = capsys.readouterr().err
    assert code != 0
    assert "andromeda" in err
    assert "did not answer" in err


def test_bare_dash_a_picker_probe_survives_a_raising_transport_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The bare picker's own probe fan-out gets the identical guard."""
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    transport = _host_transport_module()

    def raising_run_camp(host, remote_argv, **kw):
        raise FileNotFoundError("ssh: not found")

    monkeypatch.setattr(transport, "run_camp", raising_run_camp)

    code = _run(["attach", "-a"], monkeypatch)

    err = capsys.readouterr().err
    assert code != 0
    assert "andromeda" in err
    assert "omitted from the picker" in err


def test_bare_dash_a_nothing_to_offer_does_not_claim_a_silent_host_was_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A host dropped as silent was never actually checked, so the final
    refusal must not assert 'no running session on ... any declared
    machine' as though every declared machine had answered empty."""
    _hosts_env(tmp_path, monkeypatch, "silent-host")
    transport = _host_transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.Unreachable(reason="Connection timed out"),
    )

    code = _run(["attach", "-a"], monkeypatch)

    err = capsys.readouterr().err
    assert code != 0
    final_line = err.strip().splitlines()[-1]
    assert "silent-host" in final_line
    assert "did not answer" in final_line


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


def test_bare_dash_a_picker_probes_declared_machines_concurrently_via_barrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The bare `-a` picker's own probe fan-out, structurally pinned the same
    way as the ref form's above — a serial loop leaves the barrier unreleased
    for every host but the last."""
    n = 4
    host_names = [f"host{i}" for i in range(n)]
    _hosts_env(tmp_path, monkeypatch, *host_names)
    transport = _host_transport_module()

    barrier = threading.Barrier(n, timeout=5)

    def fake_run_camp(host, remote_argv, **kw):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            return transport.Unreachable(reason="barrier never released — not concurrent")
        return transport.Answered(stdout=json.dumps({"ok": True, "rows": []}), stderr="", exit_code=0)

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    code = _run(["attach", "-a"], monkeypatch)

    err = capsys.readouterr().err
    assert "barrier never released" not in err
    assert "did not answer" not in err
    assert code != 0
    assert "no running session found" in err


def test_bare_dash_a_picker_drops_a_malformed_remote_row_with_a_notice_never_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A row missing a key the picker indexes directly must be dropped with a
    stderr notice — never a `KeyError` aborting the whole widened picker,
    per `_attach_list_answer`'s own documented contract."""
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    transport = _host_transport_module()
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.Answered(
            stdout=json.dumps({"ok": True, "rows": [{"derived_name": "camp-x"}]}),
            stderr="",
            exit_code=0,
        ),
    )

    code = _run(["attach", "-a"], monkeypatch)

    err = capsys.readouterr().err
    assert code != 0
    assert "session_id" in err
    assert "no running session found" in err


def test_list_json_wire_payload_has_non_empty_rows_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`--list --json`'s own producer, driven end to end — the untested wire
    contract a producer/consumer key-name mismatch would otherwise ship
    green through."""
    _isolated_env(tmp_path, monkeypatch)
    _wire_local_session(monkeypatch, tmp_path=tmp_path)
    handoff = _host_handoff_module()
    # Minor (second review pass): `--list --json` never hands off — a
    # mutation that wrongly did so must not be able to reach a real
    # `os.execvp` in this process.
    monkeypatch.setattr(
        handoff, "handoff", lambda argv: (_ for _ in ()).throw(AssertionError(
            "must never hand off for --list --json"
        ))
    )

    code = _run(["attach", "--list", "--json"], monkeypatch)

    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["ok"] is True
    assert len(out["rows"]) == 1
    row = out["rows"][0]
    assert row["session_id"] == _UUID_A
    assert row["derived_name"] == f"camp-feat-a-{_UUID_A[:8]}"
    assert row["group"] == "g"
    assert row["slug"] == "feat-a"


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_bare_dash_a_picker_hands_off_to_a_selected_remote_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cross-host picker's consumer half of the `--list --json` wire
    contract: a non-empty remote row is merged, presented, chosen, and
    handed off through `remote_argv` by its `session_id` — never its
    dropped-in-scope `derived_name`."""
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    _wire_local_session(monkeypatch, tmp_path=tmp_path)
    transport = _host_transport_module()
    handoff = _host_handoff_module()

    remote_payload = {
        "ok": True,
        "rows": [
            {
                "session_id": "s-remote",
                "derived_name": "camp-remote-feat",
                "group": "g2",
                "slug": "remote-feat",
            }
        ],
    }
    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.Answered(
            stdout=json.dumps(remote_payload), stderr="", exit_code=0
        ),
    )
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))
    monkeypatch.setattr(sys, "stdin", _FakeTTY("2\n"))
    monkeypatch.setattr(sys, "stdout", _FakeTTY())

    code = _run(["attach", "-a"], monkeypatch)

    assert code == 0
    assert len(seen) == 1
    argv = seen[0]
    assert argv[0] == "ssh"
    assert "attach s-remote" in argv[-1]


def test_dash_a_bare_picker_local_selection_uses_the_derived_name_not_the_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 2 (second review pass, dispatch.py:1145): the bare `-a`
    picker's LOCAL row (row 1 — local rows are listed before any remote
    row) must hand off via `tmux attach -t <derived_name>` — never
    `session_id`. The sibling test above only ever exercised the remote
    selection; this is the local one it was missing."""
    _hosts_env(tmp_path, monkeypatch, "andromeda")
    env, derived, _tmux = _wire_local_session(monkeypatch, tmp_path=tmp_path)
    transport = _host_transport_module()
    handoff = _host_handoff_module()

    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.Answered(
            stdout=json.dumps({"ok": True, "rows": []}), stderr="", exit_code=0
        ),
    )
    seen: list = []
    monkeypatch.setattr(handoff, "handoff", lambda argv: seen.append(argv))
    monkeypatch.setattr(sys, "stdin", _FakeTTY("1\n"))
    monkeypatch.setattr(sys, "stdout", _FakeTTY())

    code = _run(["attach", "-a"], monkeypatch)

    assert code == 0
    assert seen == [["tmux", "attach", "-t", derived]]


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
