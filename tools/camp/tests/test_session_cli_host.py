"""Tests for `camp sessions --host <name>` — reusing the SAME relay seam
`camp list --host <name>` established (`camp.host.relay.relay_all_groups`),
per `test_camp_list_host.py`.

Drives `camp.cli.dispatch.main()` in-process (sys.argv monkeypatched), with
`camp.host.transport.run_camp` injected so no real SSH connection is ever
made. `hosts.toml` declares one real host ("andromeda") so `--host andromeda`
resolves and reaches the transport seam.

Test contract (docs/design/named-remote-host-answers.md,
task/camp-sessions-answers-for-a-named-host):
- zero / one / many session rows relay in the remote's own order, each
  gaining `host` set to the name the operator typed.
- a plain local `camp sessions --json` (no `--host`) is unaffected — pinned
  in test_session_cli.py, not here.
- a relayed row's `group`/`account` survive unchanged — the local side never
  re-attributes them.
- the remote store-failure row and the remote group-config-failure row both
  relay as rows, each gaining `host`, each keeping `ok: false`.
- unreachable / stopped-responding / identity-unknown / identity-changed /
  camp-not-resolvable / remote-refusal behave exactly as they do for `list`.
- the far side is always invoked with the all-groups + --json form.
- `--json` composes with `--host`; `--recoverable`, `--all`, `--dir`,
  `--limit`, and a positional workspace slug each refuse rather than being
  silently dropped.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _dispatch_module():
    return importlib.import_module("camp.cli.dispatch")


def _transport_module():
    return importlib.import_module("camp.host.transport")


@pytest.fixture()
def hosts_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAMP_CONFIG_DIR with hosts.toml declaring one host ("andromeda"),
    config/state dirs isolated in tmp_path."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "hosts.toml").write_text("[hosts.andromeda]\n", encoding="utf-8")
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))


def _rig(monkeypatch: pytest.MonkeyPatch, outcome, *, capture_argv: list | None = None):
    """Point transport.run_camp at a canned outcome, capturing the argv it
    was called with when *capture_argv* is given."""
    transport = _transport_module()

    def fake_run_camp(host, remote_argv, **kwargs):
        if capture_argv is not None:
            capture_argv.append(list(remote_argv))
        return outcome

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)
    return transport


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    dispatch = _dispatch_module()
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    with pytest.raises(SystemExit) as excinfo:
        dispatch.main()
    return excinfo.value.code


def _session_row(**overrides) -> dict:
    row = {
        "ok": True,
        "session_id": "sess-1",
        "cwd": "/ws/feat-x",
        "kind": "claude",
        "controllable": True,
        "name": None,
        "pid": 4242,
        "started_at": "2026-09-10T00:00:00+00:00",
        "group": "levr",
        "account": None,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# zero / one / many — the answered/relayed rows path
# ---------------------------------------------------------------------------


def test_zero_rows_human_no_stdout_exit_zero(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.Answered(stdout="[]", stderr="", exit_code=0)
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""


def test_zero_rows_json_empty_array_exit_zero(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.Answered(stdout="[]", stderr="", exit_code=0)
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out) == []


def test_one_row_relays_and_gains_host_key(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    remote_rows = [_session_row()]
    outcome = transport.Answered(stdout=json.dumps(remote_rows), stderr="", exit_code=0)
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code == 0
    rows = json.loads(captured.out)
    assert len(rows) == 1
    assert rows[0]["host"] == "andromeda"


def test_many_rows_relay_in_remote_order_never_resorted(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """Rows come back in a deliberately non-alphabetical order (zeta before
    alpha) — pinning that the local side never re-sorts them, unlike the
    local `--all-groups` sessions path, which sorts by group."""
    transport = _transport_module()
    remote_rows = [
        _session_row(session_id="zeta-session", group="zzz"),
        _session_row(session_id="alpha-session", group="aaa"),
    ]
    outcome = transport.Answered(stdout=json.dumps(remote_rows), stderr="", exit_code=0)
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code == 0
    rows = json.loads(captured.out)
    assert [r["session_id"] for r in rows] == ["zeta-session", "alpha-session"]


def test_many_rows_human_path_preserves_order(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    remote_rows = [
        _session_row(session_id="zeta-session", kind="claude", name=None),
        _session_row(session_id="alpha-session", kind="claude", name="worker"),
    ]
    outcome = transport.Answered(stdout=json.dumps(remote_rows), stderr="", exit_code=0)
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda"])

    captured = capsys.readouterr()
    assert code == 0
    lines = [ln for ln in captured.out.splitlines() if ln]
    assert lines[0].startswith("zeta-session")
    assert lines[1].startswith("alpha-session")
    assert "(worker)" in lines[1]


def test_a_row_missing_a_required_key_does_not_crash_and_other_rows_still_render(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """Version skew across the operator's two machines is the expected
    steady state: a remote camp of a different version can omit a key the
    human renderer indexes directly. It must degrade that ONE row rather
    than crash the whole answer — the other, well-formed row still prints."""
    transport = _transport_module()
    remote_rows = [
        {"ok": True, "cwd": "/ws/feat-x", "kind": "claude"},  # missing session_id
        _session_row(session_id="alpha-session"),
    ]
    outcome = transport.Answered(stdout=json.dumps(remote_rows), stderr="", exit_code=0)
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda"])

    captured = capsys.readouterr()
    assert code == 0
    assert "alpha-session" in captured.out
    assert "Traceback" not in captured.err


# ---------------------------------------------------------------------------
# Remote attribution survives — the local side never re-attributes group or
# account from local config.
# ---------------------------------------------------------------------------


def test_group_and_account_survive_unchanged_from_the_remote(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """A group name that exists on NEITHER machine's local config — proving
    the local side did not re-attribute it from its own group configs."""
    transport = _transport_module()
    remote_rows = [
        _session_row(group="a-group-declared-on-neither-machine", account="remote-only-account")
    ]
    outcome = transport.Answered(stdout=json.dumps(remote_rows), stderr="", exit_code=0)
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code == 0
    rows = json.loads(captured.out)
    assert rows[0]["group"] == "a-group-declared-on-neither-machine"
    assert rows[0]["account"] == "remote-only-account"


# ---------------------------------------------------------------------------
# The far side is always invoked with the all-groups + --json form.
# ---------------------------------------------------------------------------


def test_far_side_invoked_with_all_groups_and_json_form(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.Answered(stdout="[]", stderr="", exit_code=0)
    captured_argv: list = []
    _rig(monkeypatch, outcome, capture_argv=captured_argv)

    _run(monkeypatch, ["sessions", "--host", "andromeda"])

    assert len(captured_argv) == 1
    remote_argv = captured_argv[0]
    assert remote_argv[0] == "sessions"
    assert "--all-groups" in remote_argv
    assert "--json" in remote_argv


# ---------------------------------------------------------------------------
# The five non-relayable failure states — same shape as `list`, same messages.
# ---------------------------------------------------------------------------


def test_host_unreachable_state(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.Unreachable(reason="Connection timed out")
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code != 0
    assert "unreachable" in captured.err
    assert "andromeda" in captured.err
    rows = json.loads(captured.out)
    assert rows == [{"ok": False, "host": "andromeda", "reason": "unreachable — no response within 10s"}]


def test_host_stopped_responding_state(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.StoppedResponding(execution_timeout=60.0)
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code != 0
    assert "did not finish within 60s" in captured.err
    rows = json.loads(captured.out)
    assert rows == [{"ok": False, "host": "andromeda", "reason": "connected but did not finish within 60s"}]


def test_host_identity_unknown_state(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.IdentityUnknown()
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code != 0
    assert "no pinned key" in captured.err
    assert "ssh-keyscan" in captured.err
    rows = json.loads(captured.out)
    assert rows == [{"ok": False, "host": "andromeda", "reason": "no pinned host key"}]


def test_host_identity_changed_state(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.IdentityChanged()
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code != 0
    assert "different key" in captured.err
    assert "may be intercepted" in captured.err
    rows = json.loads(captured.out)
    assert rows == [{"ok": False, "host": "andromeda", "reason": "host key differs from the pinned key"}]


def test_identity_unknown_and_identity_changed_never_share_a_message(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()

    _rig(monkeypatch, transport.IdentityUnknown())
    _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])
    unknown_err = capsys.readouterr().err

    _rig(monkeypatch, transport.IdentityChanged())
    _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])
    changed_err = capsys.readouterr().err

    assert unknown_err != changed_err


def test_camp_not_resolvable_state(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.CampNotResolvable()
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code != 0
    assert "camp could not be run there" in captured.err
    assert "camp_bin" in captured.err
    rows = json.loads(captured.out)
    assert rows[0]["ok"] is False
    assert rows[0]["host"] == "andromeda"
    assert "reason" in rows[0]


def test_host_credentials_refused_state(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.CredentialsRefused()
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code != 0
    assert "refused every credential" in captured.err
    assert "ssh-add" in captured.err
    rows = json.loads(captured.out)
    assert rows == [{"ok": False, "host": "andromeda", "reason": "host refused our credentials"}]


def test_remote_refusal_relayed_prints_remote_stderr_unchanged(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.RemoteRefusal(
        stdout="",
        stderr="camp sessions: --limit expects a whole number, not 'x'\n",
        exit_code=1,
    )
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.err == "camp sessions: --limit expects a whole number, not 'x'\n"
    assert "unreachable" not in captured.err


# ---------------------------------------------------------------------------
# Collection failure — remote store-failure and group-config-failure rows
# both relay as rows, each gaining `host`, each keeping ok:false. Two
# separate tests: different key sets.
# ---------------------------------------------------------------------------


def test_store_failure_row_relays_with_host_and_keeps_ok_false(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    remote_rows = [
        {"ok": False, "account": "default", "reason": "enumeration timed out"},
    ]
    outcome = transport.Answered(stdout=json.dumps(remote_rows), stderr="", exit_code=0)
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code == 0
    rows = json.loads(captured.out)
    assert rows == [
        {"ok": False, "account": "default", "reason": "enumeration timed out", "host": "andromeda"}
    ]


def test_group_config_failure_row_relays_with_host_and_keeps_ok_false(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    remote_rows = [
        {"ok": False, "group": None, "reason": "levr.toml: invalid TOML"},
    ]
    outcome = transport.Answered(stdout=json.dumps(remote_rows), stderr="", exit_code=0)
    _rig(monkeypatch, outcome)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])

    captured = capsys.readouterr()
    assert code == 0
    rows = json.loads(captured.out)
    assert rows == [
        {"ok": False, "group": None, "reason": "levr.toml: invalid TOML", "host": "andromeda"}
    ]


def test_collection_failure_is_distinct_from_unreachable(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()

    remote_rows = [{"ok": False, "group": None, "reason": "levr.toml: invalid TOML"}]
    _rig(
        monkeypatch,
        transport.Answered(stdout=json.dumps(remote_rows), stderr="", exit_code=0),
    )
    _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])
    rows_collection = json.loads(capsys.readouterr().out)

    _rig(monkeypatch, transport.Unreachable(reason="Connection timed out"))
    _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])
    rows_unreachable = json.loads(capsys.readouterr().out)

    assert "group" in rows_collection[0]
    assert "group" not in rows_unreachable[0]


# ---------------------------------------------------------------------------
# Option composition — --json is honored remotely; every other existing
# `sessions` option (cli/session.py:1461-1471, plus the positional slug)
# refuses rather than being silently dropped. One test per decision.
# ---------------------------------------------------------------------------


def test_host_and_json_compose(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    _rig(monkeypatch, transport.Answered(stdout="[]", stderr="", exit_code=0))

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--json"])

    assert code == 0


def test_host_and_recoverable_refuses(hosts_env, monkeypatch, capsys) -> None:
    transport = _transport_module()
    captured_argv: list = []
    _rig(monkeypatch, transport.Answered(stdout="[]", stderr="", exit_code=0), capture_argv=captured_argv)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--recoverable"])

    captured = capsys.readouterr()
    assert code != 0
    # The distinctive phrase from --recoverable's OWN refusal branch, not the
    # generic catch-all (which would ALSO mention "--recoverable" by naming
    # the leftover token) — this is what tells the two branches apart.
    assert "own live sessions" in captured.err
    assert captured_argv == []  # never reaches the transport


def test_host_and_all_refuses(hosts_env, monkeypatch, capsys) -> None:
    transport = _transport_module()
    captured_argv: list = []
    _rig(monkeypatch, transport.Answered(stdout="[]", stderr="", exit_code=0), capture_argv=captured_argv)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--all"])

    captured = capsys.readouterr()
    assert code != 0
    # --all's OWN branch's distinctive phrase, not the catch-all's.
    assert "only widens --recoverable" in captured.err
    assert captured_argv == []


def test_host_and_dir_refuses(hosts_env, monkeypatch, capsys) -> None:
    transport = _transport_module()
    captured_argv: list = []
    _rig(monkeypatch, transport.Answered(stdout="[]", stderr="", exit_code=0), capture_argv=captured_argv)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--dir", "/tmp/somewhere"])

    captured = capsys.readouterr()
    assert code != 0
    # --dir's OWN branch's distinctive phrase, not the catch-all's.
    assert "not a local directory" in captured.err
    assert captured_argv == []


def test_host_and_limit_refuses(hosts_env, monkeypatch, capsys) -> None:
    transport = _transport_module()
    captured_argv: list = []
    _rig(monkeypatch, transport.Answered(stdout="[]", stderr="", exit_code=0), capture_argv=captured_argv)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "--limit", "5"])

    captured = capsys.readouterr()
    assert code != 0
    # --limit's OWN branch's distinctive phrase, not the catch-all's.
    assert "only widens --recoverable" in captured.err
    assert captured_argv == []


def test_host_and_positional_slug_refuses(hosts_env, monkeypatch, capsys) -> None:
    transport = _transport_module()
    captured_argv: list = []
    _rig(monkeypatch, transport.Answered(stdout="[]", stderr="", exit_code=0), capture_argv=captured_argv)

    code = _run(monkeypatch, ["sessions", "--host", "andromeda", "feat-x"])

    captured = capsys.readouterr()
    assert code != 0
    assert "feat-x" in captured.err
    assert "workspace slug" in captured.err
    assert captured_argv == []


# ---------------------------------------------------------------------------
# `camp launch --host <name> --group <group> <slug>` — the single-object
# relay (`camp.host.relay.answer_object_for_host`), not `relay_all_groups`.
# Covers the seven enumerated states of docs/design/a-session-starts-on-a-
# named-machine.md, driven by a fake `transport.run_camp` outcome per state.
# ---------------------------------------------------------------------------


def _launch_answer_stdout(**overrides) -> str:
    answer = {
        "workspace": "/workspace/ws-a",
        "session_id": "s1",
        "tmux_name": "camp-ws-a-s1",
        "account": None,
        "account_binding": {},
    }
    answer.update(overrides)
    return json.dumps(answer)


def test_launch_host_success_stdout_is_only_the_session_id(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    captured_argv: list = []
    outcome = transport.Answered(stdout=_launch_answer_stdout(), stderr="", exit_code=0)
    _rig(monkeypatch, outcome, capture_argv=captured_argv)

    code = _run(
        monkeypatch,
        ["launch", "ws-a", "--host", "andromeda", "--group", "demo"],
    )

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "s1\n"
    assert "andromeda" in captured.err
    assert "/workspace/ws-a" in captured.err
    assert captured_argv == [["launch", "ws-a", "--group", "demo", "--json"]]


def test_launch_host_success_json_form_carries_certainty_happened(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.Answered(stdout=_launch_answer_stdout(), stderr="", exit_code=0)
    _rig(monkeypatch, outcome)

    code = _run(
        monkeypatch,
        ["launch", "ws-a", "--host", "andromeda", "--group", "demo", "--json"],
    )

    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["session_id"] == "s1"
    assert payload["host"] == "andromeda"
    assert payload["certainty"] == "happened"


def test_launch_host_already_running_state_is_an_ordinary_success(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """The far side may answer with a SECOND session for a workspace already
    running one — camp's launches are not exclusive, so this is rendered
    exactly like any other successful launch, with no special casing."""
    transport = _transport_module()
    outcome = transport.Answered(
        stdout=_launch_answer_stdout(session_id="s2", tmux_name="camp-ws-a-s2"),
        stderr="",
        exit_code=0,
    )
    _rig(monkeypatch, outcome)

    code = _run(
        monkeypatch,
        ["launch", "ws-a", "--host", "andromeda", "--group", "demo"],
    )

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "s2\n"


def test_launch_host_refusal_reaches_operator_in_far_sides_own_words(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """No workspace to launch into: the far side's own camp refused. On this
    peer's transport the remote exit status is masked to 0 (Answered), so
    the discriminator is that stdout does not parse as a JSON object."""
    transport = _transport_module()
    outcome = transport.Answered(
        stdout="",
        stderr="camp launch: no workspace named 'ws-a' in group 'demo'\n",
        exit_code=0,
    )
    _rig(monkeypatch, outcome)

    code = _run(
        monkeypatch,
        ["launch", "ws-a", "--host", "andromeda", "--group", "demo"],
    )

    captured = capsys.readouterr()
    assert code != 0
    assert captured.out == ""
    assert "no workspace named 'ws-a' in group 'demo'" in captured.err
    assert "remote command failed" not in captured.err


def test_launch_host_remote_refusal_outcome_exit_code_is_camps_own(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """A transport that DOES propagate the remote's own exit code (unlike
    this peer's) still relays it verbatim rather than a fixed local code."""
    transport = _transport_module()
    outcome = transport.RemoteRefusal(
        stdout="", stderr="camp launch: directory outside allowlist\n", exit_code=7
    )
    _rig(monkeypatch, outcome)

    code = _run(
        monkeypatch,
        ["launch", "ws-a", "--host", "andromeda", "--group", "demo"],
    )

    captured = capsys.readouterr()
    assert code == 7
    assert "directory outside allowlist" in captured.err


def test_launch_host_unreachable_reports_no_session_was_started(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.Unreachable(reason="Connection timed out")
    _rig(monkeypatch, outcome)

    code = _run(
        monkeypatch,
        ["launch", "ws-a", "--host", "andromeda", "--group", "demo"],
    )

    captured = capsys.readouterr()
    assert code != 0
    assert "unreachable" in captured.err
    assert "no session was started" in captured.err


def test_launch_host_unknown_first_stderr_line_is_the_check_instruction(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.StoppedResponding(execution_timeout=60.0)
    _rig(monkeypatch, outcome)

    _run(
        monkeypatch,
        ["launch", "ws-a", "--host", "andromeda", "--group", "demo"],
    )

    captured = capsys.readouterr()
    first_line = captured.err.splitlines()[0]
    assert "camp sessions --host andromeda --group demo" in first_line
    assert first_line.startswith("camp launch: check")


def test_launch_host_unknown_does_not_claim_success_or_failure(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    outcome = transport.StoppedResponding(execution_timeout=60.0)
    _rig(monkeypatch, outcome)

    code = _run(
        monkeypatch,
        ["launch", "ws-a", "--host", "andromeda", "--group", "demo"],
    )

    captured = capsys.readouterr()
    assert code != 0
    assert captured.out == ""
    assert "launched session" not in captured.err
    assert "no session was started" not in captured.err


def test_launch_host_exit_codes_are_mutually_distinct(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()

    _rig(monkeypatch, transport.Answered(stdout=_launch_answer_stdout(), stderr="", exit_code=0))
    success_code = _run(
        monkeypatch, ["launch", "ws-a", "--host", "andromeda", "--group", "demo"]
    )
    capsys.readouterr()

    _rig(monkeypatch, transport.Unreachable(reason="Connection timed out"))
    certain_failure_code = _run(
        monkeypatch, ["launch", "ws-a", "--host", "andromeda", "--group", "demo"]
    )
    capsys.readouterr()

    _rig(monkeypatch, transport.StoppedResponding(execution_timeout=60.0))
    unknown_code = _run(
        monkeypatch, ["launch", "ws-a", "--host", "andromeda", "--group", "demo"]
    )
    capsys.readouterr()

    assert len({success_code, certain_failure_code, unknown_code}) == 3


def test_launch_host_far_side_exit_code_never_collides_with_the_unknown_code(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """A far side that refuses with the same number camp reserves for "I do not
    know" must not be reported as uncertain.

    The uncertain outcome's exit code is the one signal a scripted caller can
    branch on without parsing prose, and its whole purpose is to mean "check
    before you retry". A remote refusal is a CERTAIN failure — nothing was
    started, and there is nothing to check. Passing the far side's own status
    through unmapped lets a remote `exit 3` impersonate camp's uncertainty,
    which is exactly the collision the council item forbids.
    """
    transport = _transport_module()

    _rig(monkeypatch, transport.StoppedResponding(execution_timeout=60.0))
    unknown_code = _run(
        monkeypatch, ["launch", "ws-a", "--host", "andromeda", "--group", "demo"]
    )
    capsys.readouterr()

    _rig(
        monkeypatch,
        transport.RemoteRefusal(stdout="", stderr="camp: no workspace 'ws-a'", exit_code=unknown_code),
    )
    refusal_code = _run(
        monkeypatch, ["launch", "ws-a", "--host", "andromeda", "--group", "demo"]
    )
    err = capsys.readouterr().err

    assert refusal_code != unknown_code, (
        f"a far-side refusal exited {refusal_code}, the code reserved for the "
        f"uncertain outcome — a caller cannot tell them apart"
    )
    assert refusal_code != 0
    assert "no session was started" in err


def test_launch_host_certain_and_uncertain_first_lines_differ_in_opening_words(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()

    _rig(monkeypatch, transport.Unreachable(reason="Connection timed out"))
    _run(monkeypatch, ["launch", "ws-a", "--host", "andromeda", "--group", "demo"])
    certain_first_line = capsys.readouterr().err.splitlines()[0]

    _rig(monkeypatch, transport.StoppedResponding(execution_timeout=60.0))
    _run(monkeypatch, ["launch", "ws-a", "--host", "andromeda", "--group", "demo"])
    uncertain_first_line = capsys.readouterr().err.splitlines()[0]

    assert certain_first_line.split()[:3] != uncertain_first_line.split()[:3]


def test_launch_host_json_carries_certainty_for_a_certain_failure(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    _rig(monkeypatch, transport.Unreachable(reason="Connection timed out"))

    code = _run(
        monkeypatch,
        ["launch", "ws-a", "--host", "andromeda", "--group", "demo", "--json"],
    )

    captured = capsys.readouterr()
    assert code != 0
    payload = json.loads(captured.out)
    assert payload["certainty"] == "did_not_happen"
    assert payload["ok"] is False


def test_launch_host_json_carries_certainty_for_the_unknown_outcome(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    _rig(monkeypatch, transport.StoppedResponding(execution_timeout=60.0))

    code = _run(
        monkeypatch,
        ["launch", "ws-a", "--host", "andromeda", "--group", "demo", "--json"],
    )

    captured = capsys.readouterr()
    assert code != 0
    payload = json.loads(captured.out)
    assert payload["certainty"] == "unknown"
    assert payload["ok"] is False


def test_launch_host_far_side_invoked_with_group_slug_and_json(
    hosts_env, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    transport = _transport_module()
    captured_argv: list = []
    outcome = transport.Answered(stdout=_launch_answer_stdout(), stderr="", exit_code=0)
    _rig(monkeypatch, outcome, capture_argv=captured_argv)

    _run(monkeypatch, ["launch", "ws-a", "--host", "andromeda", "--group", "demo"])

    remote_argv = captured_argv[0]
    assert remote_argv == ["launch", "ws-a", "--group", "demo", "--json"]
