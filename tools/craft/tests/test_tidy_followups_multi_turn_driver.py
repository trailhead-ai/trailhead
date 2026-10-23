"""The multi-turn driver feeds an arm scripted answers one turn at a time.

The arm is a stub process that replays a canned stream-json transcript, one turn per
user message on stdin, and logs how many `result` events it had emitted when each
message arrived. No model is called.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
from pathlib import Path


_CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
_CASE = _CRAFT / "evals" / "tidy-followups-builds-one-parent"
_SCANNER = _CRAFT / "scripts" / "capture_scan.py"

sys.path.insert(0, str(_CASE))
_spec = importlib.util.spec_from_file_location("tidy_followups_driver", _CASE / "driver.py")
driver = importlib.util.module_from_spec(_spec)
sys.modules["tidy_followups_driver"] = driver
_spec.loader.exec_module(driver)

STUB = textwrap.dedent(
    """
    import json, queue, sys, threading, time
    turns = json.load(open(sys.argv[1]))
    log = open(sys.argv[2], "w")
    inbox = queue.Queue()
    emitted = [0]

    def reader():
        for line in sys.stdin:
            msg = json.loads(line)
            log.write(json.dumps({"content": msg["message"]["content"], "results_before": emitted[0]}) + "\\n")
            log.flush()
            inbox.put(msg)
        inbox.put(None)

    threading.Thread(target=reader, daemon=True).start()
    for turn in turns:
        if inbox.get() is None:
            break
        print(json.dumps({"type": "system", "subtype": "init", "session_id": "s"}), flush=True)
        for ev in turn:
            print(json.dumps(ev), flush=True)
            if ev.get("type") == "assistant":
                time.sleep(0.15)
        emitted[0] += 1
    """
)


def text(t, parent=None):
    return {"type": "assistant", "parent_tool_use_id": parent,
            "message": {"content": [{"type": "text", "text": t}]}}


def tool(name, **inp):
    return {"type": "assistant", "parent_tool_use_id": None,
            "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def tool_result(t):
    return {"type": "user", "parent_tool_use_id": None,
            "message": {"content": [{"type": "tool_result", "content": t}]}}


RESULT = {"type": "result", "subtype": "success", "result": "x"}


def run(tmp_path, turns, answers, first="go"):
    (tmp_path / "turns.json").write_text(json.dumps(turns))
    (tmp_path / "stub.py").write_text(STUB)
    log = tmp_path / "received.jsonl"
    res = driver.drive(
        [sys.executable, tmp_path / "stub.py", tmp_path / "turns.json", log], first, answers,
        turn_timeout=20,
    )
    received = [json.loads(x) for x in log.read_text().splitlines()]
    return res, received


def test_an_answer_goes_out_only_after_the_previous_turns_result(tmp_path):
    turns = [
        [text("Shortlist ready. Which moves?"), RESULT],
        [text("Proceed? Reply yes and the code k7q2"), RESULT],
        [text("Done."), RESULT],
    ]
    res, received = run(tmp_path, turns, ["none", "yes {code}"])
    assert [r["content"] for r in received] == ["go", "none", "yes k7q2"]
    assert [r["results_before"] for r in received] == [0, 1, 2]
    assert res.ended == "no-question"


def test_code_is_filled_from_assistant_text(tmp_path):
    turns = [[text("Proceed? Reply yes and the code k7q2"), RESULT], [text("ok"), RESULT]]
    res, received = run(tmp_path, turns, ["yes {code}"])
    assert received[1]["content"] == "yes k7q2"
    assert res.turns[0]["code"] == "k7q2"
    assert res.turns[0]["answer_sent"] == "yes k7q2"


def test_code_seen_only_in_a_tool_result_is_left_empty(tmp_path):
    turns = [
        [tool("Bash", command="preview"), tool_result("Proceed? Reply yes and the code k7q2"),
         text("Shall I continue?"), RESULT],
        [text("ok"), RESULT],
    ]
    res, received = run(tmp_path, turns, ["yes {code}"])
    assert received[1]["content"] == "yes "
    assert res.turns[0]["code"] is None
    assert res.turns[0]["display_shown"] is False


def test_the_code_a_tool_call_carries_is_not_taken(tmp_path):
    turns = [
        [tool("Bash", command="echo 'Proceed? Reply yes and the code k7q2'"),
         text("Shall I continue?"), RESULT],
        [text("ok"), RESULT],
    ]
    res, received = run(tmp_path, turns, ["yes {code}"])
    assert received[1]["content"] == "yes "


def test_an_ordinary_four_character_word_is_not_a_code(tmp_path):
    turns = [
        [text("I read the code base and the code that runs next. Anything else?"), RESULT],
        [text("Is this the code base"), text("Proceed? Reply yes and the code wxyz"), RESULT],
        [text("ok"), RESULT],
    ]
    res, received = run(tmp_path, turns, ["a {code}", "b {code}"])
    assert received[1]["content"] == "a "
    assert received[2]["content"] == "b wxyz"


def test_the_last_code_in_a_turn_wins(tmp_path):
    turns = [
        [text("Proceed? Reply yes and the code aaaa"), text("Proceed? Reply yes and the code bbbb"), RESULT],
        [text("ok"), RESULT],
    ]
    res, received = run(tmp_path, turns, ["{code}"])
    assert received[1]["content"] == "bbbb"


def test_a_code_with_a_character_outside_the_alphabet_is_not_a_code(tmp_path):
    turns = [[text("Proceed? Reply yes and the code k7q0"), text("Shall I go on?"), RESULT], [text("ok"), RESULT]]
    res, received = run(tmp_path, turns, ["x{code}"])
    assert received[1]["content"] == "x"


def test_a_nested_subagents_code_is_not_taken(tmp_path):
    turns = [
        [text("Proceed? Reply yes and the code k7q2", parent="toolu_1"),
         {"type": "result", "subtype": "success", "parent_tool_use_id": "toolu_1", "result": "sub"},
         text("Anything else?"), RESULT],
        [text("ok"), RESULT],
    ]
    res, received = run(tmp_path, turns, ["yes {code}"])
    assert received[1]["content"] == "yes "
    assert "k7q2" not in res.turns[0]["assistant_text"]
    assert res.turns[0]["assistant_text"] == "Anything else?"


def test_a_turn_ending_without_a_question_stops_and_records_unsent_answers(tmp_path):
    turns = [[text("All done."), RESULT]]
    res, received = run(tmp_path, turns, ["one", "two"])
    assert [r["content"] for r in received] == ["go"]
    assert res.unsent == ["one", "two"]
    assert res.ended == "no-question"
    assert res.turns[0]["answer_sent"] is None


def test_a_trailing_question_mark_counts_as_a_question(tmp_path):
    turns = [[text("Which one do you want?"), RESULT], [text("ok."), RESULT]]
    res, received = run(tmp_path, turns, ["the first"])
    assert received[1]["content"] == "the first"
    assert res.turns[0]["asked"] is True
    assert res.turns[1]["asked"] is False


def test_running_out_of_answers_while_asked_ends_the_run(tmp_path):
    turns = [[text("Which?"), RESULT]]
    res, received = run(tmp_path, turns, [])
    assert res.ended == "answers-exhausted"
    assert res.unsent == []


def test_the_record_carries_text_calls_answer_and_display_flag(tmp_path):
    turns = [
        [text("Looking."), tool("Bash", command="ls"), text("Proceed? Reply yes and the code k7q2"), RESULT],
        [text("ok"), RESULT],
    ]
    res, _ = run(tmp_path, turns, ["yes {code}"])
    t = res.turns[0]
    assert t["turn"] == 1
    assert t["assistant_text"] == "Looking.\nProceed? Reply yes and the code k7q2"
    assert t["tool_calls"] == [{"name": "Bash", "input": {"command": "ls"}}]
    assert t["answer_sent"] == "yes k7q2"
    assert t["display_shown"] is True
    assert res.turns[1]["display_shown"] is False


def test_the_recorded_transcript_passes_the_capture_scrub(tmp_path):
    leaky = "/Users/dev/.local/state/camp/g/worktrees/w/trailhead/x rev " + "a" * 40
    turns = [[text(f"Read {leaky}. Proceed? Reply yes and the code k7q2"),
              tool("Bash", command=f"cat {leaky}"), RESULT], [text("ok"), RESULT]]
    res, _ = run(tmp_path, turns, ["yes {code}"])
    out = tmp_path / "t.json"
    out.write_text(json.dumps(driver.transcript(res, home="/Users/dev", user="dev")))
    assert "a" * 40 not in out.read_text()
    scan = subprocess.run([sys.executable, str(_SCANNER), str(out)], capture_output=True, text=True)
    assert scan.returncode == 0, scan.stdout


def test_a_hung_turn_times_out_and_is_recorded(tmp_path):
    (tmp_path / "hang.py").write_text("import time\ntime.sleep(60)\n")
    res = driver.drive([sys.executable, tmp_path / "hang.py"], "go", ["x"], turn_timeout=0.5)
    assert res.ended == "timeout"
    assert res.unsent == ["x"]
