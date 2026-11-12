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
    check_proxied_curl_reaches_the_command_route,
    check_proxied_curl_to_another_loopback_port_is_refused,
    check_registering_without_the_authority_is_refused,
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
    "command_route": '{"reason":"credential_missing"}\nhttp=401',
    "other_loopback_port": "\nhttp=403",
    "register_without_authority": '{"reason":"registration_authority_required"}\nhttp=401',
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
    "route unreachable through the proxy": (
        check_proxied_curl_reaches_the_command_route,
        GOOD_FACTS,
        {"command_route": "http=403"},
        "http=403",
    ),
    "route answered 401 for another reason": (
        check_proxied_curl_reaches_the_command_route,
        GOOD_FACTS,
        {"command_route": '{"reason":"credential_unrecognised"}\nhttp=401'},
        "credential_missing",
    ),
    "route refused by the proxy": (
        check_proxied_curl_reaches_the_command_route,
        GOOD_FACTS,
        {"command_route": '{"reason":"credential_missing"}\nhttp=403'},
        "did not answer 401",
    ),
    "registration answered 401 for another reason": (
        check_registering_without_the_authority_is_refused,
        GOOD_FACTS,
        {"register_without_authority": '{"reason":"credential_missing"}\nhttp=401'},
        "was not refused",
    ),
    "registration reason without the 401 status": (
        check_registering_without_the_authority_is_refused,
        GOOD_FACTS,
        {"register_without_authority": '{"reason":"registration_authority_required"}\nhttp=200'},
        "was not refused",
    ),
    "other loopback port open": (
        check_proxied_curl_to_another_loopback_port_is_refused,
        GOOD_FACTS,
        {"other_loopback_port": "http=200"},
        "did not refuse",
    ),
    "registration accepted without the authority": (
        check_registering_without_the_authority_is_refused,
        GOOD_FACTS,
        {"register_without_authority": "http=200"},
        "was not refused",
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
        ("command_route", check_proxied_curl_reaches_the_command_route),
        ("other_loopback_port", check_proxied_curl_to_another_loopback_port_is_refused),
        ("register_without_authority", check_registering_without_the_authority_is_refused),
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
  prev="$a"
  case "$a" in http://*) url="$a" ;; esac
done
host=${url#http://}; host=${host%%[:/]*}
case ",$NO_PROXY," in
  *",$host,"*) [ "$override" = 1 ] && echo proxy || echo direct ;;
  *) echo proxy ;;
esac
"""


def test_the_probe_commands_reach_the_proxy_even_though_loopback_is_in_no_proxy(tmp_path):
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
    steps = sandbox_steps(tmp_path, 7000, 7001, tmp_path / "shims" / "outpost-gateway")
    for name in ("command_route", "other_loopback_port", "register_without_authority"):
        out = subprocess.run(["sh", "-c", steps[name]], env=env, capture_output=True, text=True).stdout
        assert out.strip() == "proxy", name
