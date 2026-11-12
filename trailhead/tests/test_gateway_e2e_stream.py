"""The stream reader the opt-in gateway end-to-end test uses on ``claude -p`` output (runs ungated)."""

import json

from ._gateway_e2e_support import tool_results


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
