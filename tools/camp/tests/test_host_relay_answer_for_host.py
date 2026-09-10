"""Tests for `camp.host.relay.answer_for_host` and `HostAnswer` — the
non-exiting, non-printing per-host answer `relay_all_groups` is built on.

`relay_all_groups` prints and exits; `answer_for_host` must never do either.
It is the seam a later merged-answer surface calls once per declared host
from inside a thread pool, so it must return the same value shape for every
transport outcome and hold nothing across calls.

Covers each of the eleven transport-reachable named-host states (the
twelfth, "host not declared", is refused before a host is ever resolved and
never reaches this seam — see the task report for the enumeration).
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _relay_module():
    return importlib.import_module("camp.host.relay")


def _transport_module():
    return importlib.import_module("camp.host.transport")


def _config_module():
    return importlib.import_module("camp.host.config")


def _host():
    config = _config_module()
    return config.Host(ssh="andromeda", camp_bin="camp")


def _answer(monkeypatch, outcome):
    """Points `transport.run_camp` at a canned outcome — the same seam
    `test_camp_list_host.py`'s `_rig` stubs — then calls `answer_for_host`
    directly, bypassing the CLI dispatcher entirely."""
    relay = _relay_module()
    transport = _transport_module()

    def fake_run_camp(host, remote_argv, **kwargs):
        return outcome

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    return relay.answer_for_host(
        "list", _host(), "andromeda", ["list", "--all-groups", "--json"],
    )


# ---------------------------------------------------------------------------
# answered — zero / one / many rows, host-stamped, order preserved
# ---------------------------------------------------------------------------


def test_answered_zero_rows(monkeypatch) -> None:
    transport = _transport_module()
    answer = _answer(monkeypatch, transport.Answered(stdout="[]", stderr="", exit_code=0))
    assert answer.rows == []
    assert answer.exit_code == 0


def test_answered_one_row_stamped_with_host(monkeypatch) -> None:
    transport = _transport_module()
    answer = _answer(
        monkeypatch,
        transport.Answered(stdout='[{"ok": true, "slug": "ws-a", "workspace_path": "/a"}]', stderr="", exit_code=0),
    )
    assert answer.rows == [{"ok": True, "slug": "ws-a", "workspace_path": "/a", "host": "andromeda"}]


def test_answered_many_rows_order_preserved_not_resorted(monkeypatch) -> None:
    transport = _transport_module()
    stdout = '[{"ok": true, "slug": "zeta"}, {"ok": true, "slug": "alpha"}]'
    answer = _answer(monkeypatch, transport.Answered(stdout=stdout, stderr="", exit_code=0))
    assert [row["slug"] for row in answer.rows] == ["zeta", "alpha"]
    assert all(row["host"] == "andromeda" for row in answer.rows)


def test_answered_ok_row_not_touched_beyond_host_stamp(monkeypatch) -> None:
    """No key added, removed, renamed, or reordered beyond the `host` stamp."""
    transport = _transport_module()
    stdout = '[{"ok": true, "slug": "ws-a", "branch": "b", "workspace_path": "/a", "group": "g"}]'
    answer = _answer(monkeypatch, transport.Answered(stdout=stdout, stderr="", exit_code=0))
    assert answer.rows == [
        {"ok": True, "slug": "ws-a", "branch": "b", "workspace_path": "/a", "group": "g", "host": "andromeda"}
    ]


def test_answered_carries_remote_exit_code_and_stderr_notice(monkeypatch) -> None:
    transport = _transport_module()
    answer = _answer(
        monkeypatch,
        transport.Answered(stdout="[]", stderr="camp list: levr.toml: invalid TOML — skipping\n", exit_code=0),
    )
    assert answer.exit_code == 0
    assert answer.notices == ["camp list: levr.toml: invalid TOML — skipping"]


def test_answer_for_host_never_calls_sys_exit_or_prints(monkeypatch, capsys) -> None:
    transport = _transport_module()
    _answer(monkeypatch, transport.Answered(stdout="[]", stderr="a stderr line\n", exit_code=3))
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_answer_for_host_has_no_cross_call_state(monkeypatch) -> None:
    """Calling it twice with different outcomes never leaks state between
    calls — required for safe use from a thread pool, one call per host."""
    transport = _transport_module()
    first = _answer(monkeypatch, transport.Answered(stdout='[{"ok": true, "slug": "a"}]', stderr="", exit_code=0))
    second = _answer(monkeypatch, transport.Unreachable(reason="Connection timed out"))
    assert first.rows == [{"ok": True, "slug": "a", "host": "andromeda"}]
    assert second.rows == [
        {"ok": False, "host": "andromeda", "reason": "unreachable — no response within 10s"}
    ]


# ---------------------------------------------------------------------------
# the six local-classified failure states — one ok:false row, own reason
# ---------------------------------------------------------------------------


def test_unreachable_reason_and_row(monkeypatch) -> None:
    transport = _transport_module()
    answer = _answer(monkeypatch, transport.Unreachable(reason="Connection timed out"))
    assert answer.rows == [
        {"ok": False, "host": "andromeda", "reason": "unreachable — no response within 10s"}
    ]
    assert answer.exit_code == 1
    assert any("unreachable" in n for n in answer.notices)


def test_stopped_responding_reason_and_row(monkeypatch) -> None:
    transport = _transport_module()
    answer = _answer(monkeypatch, transport.StoppedResponding(execution_timeout=60.0))
    assert answer.rows == [
        {"ok": False, "host": "andromeda", "reason": "connected but did not finish within 60s"}
    ]
    assert answer.exit_code == 1


def test_identity_unknown_reason_and_row(monkeypatch) -> None:
    transport = _transport_module()
    answer = _answer(monkeypatch, transport.IdentityUnknown())
    assert answer.rows == [{"ok": False, "host": "andromeda", "reason": "no pinned host key"}]
    assert any("ssh-keyscan" in n for n in answer.notices)


def test_identity_changed_reason_and_row(monkeypatch) -> None:
    transport = _transport_module()
    answer = _answer(monkeypatch, transport.IdentityChanged())
    assert answer.rows == [
        {"ok": False, "host": "andromeda", "reason": "host key differs from the pinned key"}
    ]
    assert any("may be intercepted" in n for n in answer.notices)


def test_camp_not_resolvable_reason_and_row(monkeypatch) -> None:
    transport = _transport_module()
    answer = _answer(monkeypatch, transport.CampNotResolvable())
    assert answer.rows == [
        {
            "ok": False,
            "host": "andromeda",
            "reason": "camp could not be run on the host — declare camp_bin for this host in hosts.toml",
        }
    ]


def test_credentials_refused_reason_and_row(monkeypatch) -> None:
    transport = _transport_module()
    answer = _answer(monkeypatch, transport.CredentialsRefused())
    assert answer.rows == [
        {"ok": False, "host": "andromeda", "reason": "host refused our credentials"}
    ]
    assert any("ssh-add" in n for n in answer.notices)


# ---------------------------------------------------------------------------
# remote refusal / unparsable answer
# ---------------------------------------------------------------------------


def test_remote_refusal_relayed_row_and_exit_code(monkeypatch) -> None:
    transport = _transport_module()
    answer = _answer(
        monkeypatch,
        transport.RemoteRefusal(stdout="", stderr="ssh: some unmatched transport failure\n", exit_code=255),
    )
    assert answer.rows == [
        {"ok": False, "host": "andromeda", "reason": "remote answer could not be parsed"}
    ]
    assert answer.exit_code == 255


def test_unparsable_json_array_of_non_objects_row(monkeypatch) -> None:
    transport = _transport_module()
    answer = _answer(monkeypatch, transport.Answered(stdout='["oops", "not", "objects"]', stderr="", exit_code=0))
    assert answer.rows == [
        {"ok": False, "host": "andromeda", "reason": "remote answer could not be parsed"}
    ]
