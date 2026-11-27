"""The stream reader and the checks the opt-in gateway end-to-end test applies to a sandboxed session (ungated)."""

import json
import subprocess
from pathlib import Path

import pytest

from ._gateway_e2e_support import (
    CHECKS,
    Facts,
    Session,
    check_credential_file_read_is_refused,
    check_hook_registered_the_session_for_w1,
    check_the_command_route_is_not_served_over_tcp,
    check_the_command_route_socket_is_refused_inside_the_sandbox,
    check_proxied_curl_to_another_loopback_port_is_refused,
    check_registering_over_the_socket_is_refused_inside_the_sandbox,
    check_run_list_prints_the_empty_list,
    sandbox_steps,
    tool_results,
)


def _use(i, cmd):
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": i, "name": "Bash", "input": {"command": cmd}}]},
    }


def _result(i, body):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": i, "content": body}]}}


def test_each_command_gets_its_own_result_text_and_a_call_without_a_result_is_left_out():
    events = [
        _use("a", "one"),
        _result("a", "first"),
        _use("b", "two"),
        _result("b", [{"type": "text", "text": "sec"}, {"type": "text", "text": "ond"}]),
        _use("c", "three"),
    ]
    stream = "\n".join([*map(json.dumps, events), "not json"])
    assert tool_results(stream) == {"one": "first", "two": "second"}


def test_a_non_bash_tool_call_is_not_reported():
    event = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": "x", "name": "Read", "input": {"file_path": "/f"}}]},
    }
    assert tool_results("\n".join([json.dumps(event), json.dumps(_result("x", "body"))])) == {}


GOOD_FACTS = Facts([("ws-1-2", 0o600)], [("ws-1-2", "e2e/ws-one")])
GOOD_OUTPUTS = {
    "credential_read": "Exit code 1\ncat: x: No such file or directory",
    "run_list": "No runs in workspace e2e/ws-one\n",
    "command_route": "Exit code 7\ncurl: (7) Couldn't connect to server\n\nhttp=000\n",
    "tcp_command_route": '{"reason":"gateway_not_on_tcp"}\nhttp=404',
    "other_loopback_port": "\nhttp=403",
    "register_without_authority": "Exit code 7\ncurl: (7) Couldn't connect to server\n\nhttp=000\n",
}


def _session(**changes):
    outputs = {**GOOD_OUTPUTS, **changes}
    return Session({k: v for k, v in outputs.items() if v is not None}, 0, Path("stream.jsonl"))


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_every_check_passes_on_a_good_session(check):
    check(GOOD_FACTS, _session())


# (check, facts, outputs that change, what the failure must say)
BROKEN = {
    "no hook, nothing registered": (
        check_hook_registered_the_session_for_w1,
        Facts([], []),
        {},
        "no credential file",
    ),
    "registered for the wrong workspace": (
        check_hook_registered_the_session_for_w1,
        Facts([("ws-1-2", 0o600)], [("ws-1-2", "e2e/ws-two")]),
        {},
        "ws-two",
    ),
    "credential file too open": (
        check_hook_registered_the_session_for_w1,
        Facts([("ws-1-2", 0o644)], [("ws-1-2", "e2e/ws-one")]),
        {},
        "0o644",
    ),
    "credential readable": (
        check_credential_file_read_is_refused,
        GOOD_FACTS,
        {"credential_read": ""},
        "was not refused",
    ),
    "read failed for another reason than the folder being hidden": (
        check_credential_file_read_is_refused,
        GOOD_FACTS,
        {"credential_read": "Exit code 1\ncat: x: Is a directory"},
        "was not refused",
    ),
    "no credential existed to refuse": (
        check_credential_file_read_is_refused,
        Facts([], []),
        {},
        "proves nothing",
    ),
    "run list without a credential": (
        check_run_list_prints_the_empty_list,
        GOOD_FACTS,
        {"run_list": "outpost-gateway: this session has no gateway credential"},
        "no gateway credential",
    ),
    "run list for another workspace": (
        check_run_list_prints_the_empty_list,
        GOOD_FACTS,
        {"run_list": "No runs in workspace e2e/ws-two"},
        "ws-two",
    ),
    "command route answered 401 over the socket from inside the sandbox": (
        check_the_command_route_socket_is_refused_inside_the_sandbox,
        GOOD_FACTS,
        {"command_route": '{"reason":"credential_missing"}\nhttp=401'},
        "was not refused by the sandbox",
    ),
    "command route answered 200 over the socket from inside the sandbox": (
        check_the_command_route_socket_is_refused_inside_the_sandbox,
        GOOD_FACTS,
        {"command_route": '{"runs":[]}\nhttp=200'},
        "was not refused by the sandbox",
    ),
    "command route answered 401 without a recognised gateway reason": (
        check_the_command_route_socket_is_refused_inside_the_sandbox,
        GOOD_FACTS,
        {"command_route": "Exit code 22\ncurl: (22) The requested URL returned error: 401\nhttp=401"},
        "was not refused by the sandbox",
    ),
    "command route answered 200 without a recognised gateway reason": (
        check_the_command_route_socket_is_refused_inside_the_sandbox,
        GOOD_FACTS,
        {"command_route": "Exit code 1\nhttp=200"},
        "was not refused by the sandbox",
    ),
    "command route socket refused but the gateway's reason shows": (
        check_the_command_route_socket_is_refused_inside_the_sandbox,
        GOOD_FACTS,
        {"command_route": "Exit code 7\ncredential_missing\nhttp=000"},
        "got an answer from the gateway",
    ),
    "command route socket probe printed nothing but succeeded": (
        check_the_command_route_socket_is_refused_inside_the_sandbox,
        GOOD_FACTS,
        {"command_route": "http=000"},
        "was not refused by the sandbox",
    ),
    "route still served over TCP": (
        check_the_command_route_is_not_served_over_tcp,
        GOOD_FACTS,
        {"tcp_command_route": '{"reason":"credential_missing"}\nhttp=401'},
        "not refused over TCP",
    ),
    "TCP 401 carrying the TCP refusal reason": (
        check_the_command_route_is_not_served_over_tcp,
        GOOD_FACTS,
        {"tcp_command_route": '{"reason":"gateway_not_on_tcp"}\nhttp=401'},
        "not refused over TCP",
    ),
    "TCP 404 for another reason": (
        check_the_command_route_is_not_served_over_tcp,
        GOOD_FACTS,
        {"tcp_command_route": '{"reason":"not_found"}\nhttp=404'},
        "not refused over TCP",
    ),
    "TCP route refused by the proxy instead of by Outpost": (
        check_the_command_route_is_not_served_over_tcp,
        GOOD_FACTS,
        {"tcp_command_route": "http=403"},
        "not refused over TCP",
    ),
    "registration answered 401 over the socket from inside the sandbox": (
        check_registering_over_the_socket_is_refused_inside_the_sandbox,
        GOOD_FACTS,
        {"register_without_authority": '{"reason":"registration_authority_required"}\nhttp=401'},
        "was not refused by the sandbox",
    ),
    "other loopback port open": (
        check_proxied_curl_to_another_loopback_port_is_refused,
        GOOD_FACTS,
        {"other_loopback_port": "http=200"},
        "did not refuse",
    ),
    "registration accepted over the socket from inside the sandbox": (
        check_registering_over_the_socket_is_refused_inside_the_sandbox,
        GOOD_FACTS,
        {"register_without_authority": "http=200"},
        "was not refused by the sandbox",
    ),
}


@pytest.mark.parametrize("label", BROKEN)
def test_a_check_fails_for_its_own_reason_on_the_session_that_breaks_it(label):
    check, facts, changes, reason = BROKEN[label]
    with pytest.raises(AssertionError, match=reason):
        check(facts, _session(**changes))


@pytest.mark.parametrize(
    "step,check",
    [
        ("credential_read", check_credential_file_read_is_refused),
        ("run_list", check_run_list_prints_the_empty_list),
        ("command_route", check_the_command_route_socket_is_refused_inside_the_sandbox),
        ("tcp_command_route", check_the_command_route_is_not_served_over_tcp),
        ("other_loopback_port", check_proxied_curl_to_another_loopback_port_is_refused),
        ("register_without_authority", check_registering_over_the_socket_is_refused_inside_the_sandbox),
    ],
)
def test_a_step_the_session_never_ran_fails_its_check_instead_of_passing(step, check):
    with pytest.raises(AssertionError, match="was not run as written"):
        check(GOOD_FACTS, _session(**{step: None}))


_FAKE_CURL = """#!/bin/sh
# Routes the way real curl does: NO_PROXY hosts go direct unless --noproxy '' overrides it.
override=0
prev=""
for a in "$@"; do
  if [ "$prev" = "--noproxy" ] && [ -z "$a" ]; then override=1; fi
  if [ "$prev" = "--unix-socket" ]; then sock="$a"; fi
  prev="$a"
  case "$a" in http://*) url="$a" ;; esac
done
if [ -n "$sock" ]; then
  echo "curl: (7) Couldn't connect to server" >&2
  case " $* " in *" -w "*) printf '\nhttp=000\n' ;; esac
  exit 7
fi
host=${url#http://}; host=${host%%[:/]*}
case ",$NO_PROXY," in
  *",$host,"*) [ "$override" = 1 ] && echo proxy || echo direct ;;
  *) echo proxy ;;
esac
"""


def test_the_probe_commands_reach_their_target_the_way_the_gateway_is_served(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(_FAKE_CURL)
    curl.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1",
    }
    steps = sandbox_steps(tmp_path, 7001, tmp_path / "shims" / "outpost-gateway", 7000)
    for name in ("other_loopback_port", "tcp_command_route"):
        out = subprocess.run(["sh", "-c", steps[name]], env=env, capture_output=True, text=True).stdout
        assert out.strip() == "proxy", name


def _as_the_session_saw_it(command, env):
    done = subprocess.run(["sh", "-c", command], env=env, capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else f"Exit code {done.returncode}\n{done.stderr}{done.stdout}"


def test_a_session_whose_socket_probes_are_refused_satisfies_the_socket_checks(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text(_FAKE_CURL)
    (bin_dir / "curl").chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HTTP_PROXY": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1"}
    steps = sandbox_steps(tmp_path, 7001, tmp_path / "shims" / "outpost-gateway", 7000)
    session = _session(
        command_route=_as_the_session_saw_it(steps["command_route"], env),
        register_without_authority=_as_the_session_saw_it(steps["register_without_authority"], env),
    )
    check_the_command_route_socket_is_refused_inside_the_sandbox(GOOD_FACTS, session)
    check_registering_over_the_socket_is_refused_inside_the_sandbox(GOOD_FACTS, session)
