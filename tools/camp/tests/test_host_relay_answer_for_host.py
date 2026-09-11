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
import json
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


# ---------------------------------------------------------------------------
# `answer_object_for_host` — the single-object relay shape, and the
# outcome-certainty mapping every state-changing verb reuses.
# ---------------------------------------------------------------------------


def _object_answer(monkeypatch, outcome):
    """Mirrors `_answer` above, but drives the single-object relay entry
    point `answer_object_for_host` instead of the rows relay."""
    relay = _relay_module()
    transport = _transport_module()

    def fake_run_camp(host, remote_argv, **kwargs):
        return outcome

    monkeypatch.setattr(transport, "run_camp", fake_run_camp)

    return relay.answer_object_for_host(
        "launch", _host(), "andromeda", ["launch", "ws-a", "--group", "g", "--json"],
    )


_LAUNCH_ANSWER_STDOUT = (
    '{"workspace": "/a", "session_id": "s1", "tmux_name": "t1", '
    '"account": "work", "account_binding": {"HOME": "/home/work"}}'
)


def test_certainty_answered_is_happened(monkeypatch) -> None:
    transport = _transport_module()
    answer = _object_answer(
        monkeypatch, transport.Answered(stdout=_LAUNCH_ANSWER_STDOUT, stderr="", exit_code=0)
    )
    assert answer.certainty == _relay_module().Certainty.HAPPENED


def test_certainty_stopped_responding_is_unknown(monkeypatch) -> None:
    transport = _transport_module()
    answer = _object_answer(monkeypatch, transport.StoppedResponding(execution_timeout=60.0))
    assert answer.certainty == _relay_module().Certainty.UNKNOWN


def test_certainty_unreachable_is_did_not_happen(monkeypatch) -> None:
    transport = _transport_module()
    answer = _object_answer(monkeypatch, transport.Unreachable(reason="Connection timed out"))
    assert answer.certainty == _relay_module().Certainty.DID_NOT_HAPPEN


def test_certainty_identity_unknown_is_did_not_happen(monkeypatch) -> None:
    transport = _transport_module()
    answer = _object_answer(monkeypatch, transport.IdentityUnknown())
    assert answer.certainty == _relay_module().Certainty.DID_NOT_HAPPEN


def test_certainty_identity_changed_is_did_not_happen(monkeypatch) -> None:
    transport = _transport_module()
    answer = _object_answer(monkeypatch, transport.IdentityChanged())
    assert answer.certainty == _relay_module().Certainty.DID_NOT_HAPPEN


def test_certainty_credentials_refused_is_did_not_happen(monkeypatch) -> None:
    transport = _transport_module()
    answer = _object_answer(monkeypatch, transport.CredentialsRefused())
    assert answer.certainty == _relay_module().Certainty.DID_NOT_HAPPEN


def test_certainty_camp_not_resolvable_is_did_not_happen(monkeypatch) -> None:
    transport = _transport_module()
    answer = _object_answer(monkeypatch, transport.CampNotResolvable())
    assert answer.certainty == _relay_module().Certainty.DID_NOT_HAPPEN


def test_certainty_remote_refusal_is_did_not_happen(monkeypatch) -> None:
    transport = _transport_module()
    answer = _object_answer(
        monkeypatch,
        transport.RemoteRefusal(stdout="", stderr="camp launch: no such group\n", exit_code=1),
    )
    assert answer.certainty == _relay_module().Certainty.DID_NOT_HAPPEN


def test_stopped_responding_is_the_only_unknown_outcome() -> None:
    """Enumerates every transport outcome type by reflection, so a later
    outcome added to `camp.host.transport` without a certainty mapping
    entry fails this test rather than silently defaulting to certain."""
    transport = _transport_module()
    relay = _relay_module()

    sample_outcomes = {
        transport.Answered: transport.Answered(stdout="{}", stderr="", exit_code=0),
        transport.Unreachable: transport.Unreachable(reason="x"),
        transport.StoppedResponding: transport.StoppedResponding(execution_timeout=60.0),
        transport.IdentityUnknown: transport.IdentityUnknown(),
        transport.IdentityChanged: transport.IdentityChanged(),
        transport.CampNotResolvable: transport.CampNotResolvable(),
        transport.CredentialsRefused: transport.CredentialsRefused(),
        transport.RemoteRefusal: transport.RemoteRefusal(stdout="", stderr="", exit_code=1),
    }
    subclasses = set(transport.TransportOutcome.__subclasses__())
    assert subclasses == set(sample_outcomes), (
        "a transport outcome type was added or removed without updating this "
        "test's enumeration"
    )

    unknown_types = {
        outcome_type
        for outcome_type, outcome in sample_outcomes.items()
        if relay.classify_certainty(outcome) == relay.Certainty.UNKNOWN
    }
    assert unknown_types == {transport.StoppedResponding}


def test_object_answer_relays_json_object_successfully(monkeypatch) -> None:
    transport = _transport_module()
    answer = _object_answer(
        monkeypatch, transport.Answered(stdout=_LAUNCH_ANSWER_STDOUT, stderr="", exit_code=0)
    )
    assert answer.answer == {
        "workspace": "/a",
        "session_id": "s1",
        "tmux_name": "t1",
        "account": "work",
        "account_binding": {"HOME": "/home/work"},
    }


def test_same_object_payload_does_not_relay_through_rows_relay(monkeypatch) -> None:
    """Pins why the second shape exists: an object answer is not a rows
    answer — the existing relay treats it as unparsable."""
    transport = _transport_module()
    answer = _answer(monkeypatch, transport.Answered(stdout=_LAUNCH_ANSWER_STDOUT, stderr="", exit_code=0))
    assert answer.rows == [
        {"ok": False, "host": "andromeda", "reason": "remote answer could not be parsed"}
    ]


def test_object_relay_stderr_survives_verbatim_apart_from_stripping(monkeypatch) -> None:
    transport = _transport_module()
    stderr = "camp launch: \x1b[31mno such group\x1b[0m: 'nope'\n"
    answer = _object_answer(
        monkeypatch, transport.RemoteRefusal(stdout="", stderr=stderr, exit_code=1)
    )
    assert answer.notices == ["camp launch: [31mno such group[0m: 'nope'"]


def test_object_relay_exit_code_propagates_unchanged(monkeypatch) -> None:
    transport = _transport_module()
    answer = _object_answer(
        monkeypatch, transport.RemoteRefusal(stdout="", stderr="refused\n", exit_code=17)
    )
    assert answer.exit_code == 17


def test_object_relay_exit_code_propagates_nonzero_alongside_well_formed_answer(monkeypatch) -> None:
    """A well-formed answer is relayed even when the far side's own exit
    code is non-zero — the exit code is carried unchanged, not coerced."""
    transport = _transport_module()
    answer = _object_answer(
        monkeypatch, transport.Answered(stdout=_LAUNCH_ANSWER_STDOUT, stderr="", exit_code=9)
    )
    assert answer.answer is not None
    assert answer.exit_code == 9


def test_control_sequences_stripped_from_every_success_field(monkeypatch) -> None:
    """The success path is the one an operator trusts most — control
    sequences must be stripped from every field, not only refusal text."""
    transport = _transport_module()
    poisoned_stdout = json.dumps({
        "workspace": "/a\x1b[2J",
        "session_id": "s1\x1b[31m",
        "tmux_name": "t1\x9b8m",
        "account": "work",
        "account_binding": {"HOME": "/home/work\x1b[0m"},
    })
    answer = _object_answer(
        monkeypatch, transport.Answered(stdout=poisoned_stdout, stderr="", exit_code=0)
    )
    assert answer.answer["workspace"] == "/a[2J"
    assert answer.answer["session_id"] == "s1[31m"
    assert answer.answer["tmux_name"] == "t18m"
    assert answer.answer["account_binding"] == {"HOME": "/home/work[0m"}


def test_answered_with_unparsable_stdout_is_not_a_relayable_success(monkeypatch) -> None:
    """`Answered` alone does not guarantee a relayable success — over a
    transport that does not propagate the remote's own exit status, it also
    covers camp-not-installed and camp-crashed, whose stdout is not a JSON
    object. The caller must be able to tell those apart from a parsed
    answer, so a non-object stdout on an `Answered` outcome still yields
    `answer=None` even though the certainty is `HAPPENED`."""
    transport = _transport_module()
    relay = _relay_module()
    answer = _object_answer(
        monkeypatch, transport.Answered(stdout="not json at all", stderr="", exit_code=0)
    )
    assert answer.answer is None
    assert answer.certainty == relay.Certainty.HAPPENED


def test_answered_with_json_array_stdout_is_not_a_relayable_success(monkeypatch) -> None:
    """A JSON *array* (the rows shape) is well-formed JSON but not an
    object — it must not be mistaken for a relayable single-object answer."""
    transport = _transport_module()
    answer = _object_answer(
        monkeypatch, transport.Answered(stdout='[{"session_id": "s1"}]', stderr="", exit_code=0)
    )
    assert answer.answer is None
