"""Behavioural tests for the scratch-pad kit's reply builder.

Every test builds a real pad with ``scratch_pad.build_pad`` from the real kit,
opens it from ``file://`` through the probe harness, calls ``PadReply.build``
inside the page, and parses the fenced body back out in Python.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

import chromium_harness as ch

SKILL_DIR = Path(__file__).parent.parent / "plugins" / "outpost" / "skills" / "scratch-pad"

_spec = importlib.util.spec_from_file_location("scratch_pad", SKILL_DIR / "scratch_pad.py")
scratch_pad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scratch_pad)

HOSTILE_TEXT = 'q" \\ back\nline ``` fence <script>window.pwned=1</script> end'
CONTROL_NOTE = "a b c\td\u0001e " + "x" * 20000


def _answer(pick=None, not_=(), none=False, note=""):
    return {"pick": pick, "not": list(not_), "none": none, "note": note}


def _round(prompts=("Header layout",), title="Settings page", n_options=3):
    return {
        "title": title,
        "intro": "Where should the controls live?",
        "questions": [
            {
                "prompt": prompt,
                "options": [
                    {"name": f"opt {i}", "cost": f"cost {i}", "pick": i == 0, "layout": {"rule": True}}
                    for i in range(n_options)
                ],
            }
            for prompt in prompts
        ],
    }


def _probe(tmp_path, data, js):
    env = {
        "OUTPOST_STATE_DIR": str(tmp_path / "state"),
        "XDG_STATE_HOME": str(tmp_path / "xdg"),
        "HOME": str(tmp_path / "home"),
    }
    round_path = tmp_path / "round.json"
    round_path.write_text(json.dumps(data))
    _, pad = scratch_pad.build_pad(round_path, "demo", env=env)
    return ch.run_probe(pad / "round-1", js)


def _text(tmp_path, data, answers):
    return _probe(tmp_path, data, f"return window.PadReply.build(window.PAD_ROUND, {json.dumps(answers)});")


def _body(text):
    lines = text.split("\n")
    assert lines[0] == "```json"
    assert lines[-1] == "```"
    return json.loads("\n".join(lines[1:-1]))


def _reply(tmp_path, data, answers):
    return _body(_text(tmp_path, data, answers))


@pytest.mark.parametrize(("index", "letter"), [(0, "A"), (1, "B"), (2, "C"), (None, None)])
def test_pick_index_becomes_its_letter(tmp_path, index, letter):
    got = _reply(tmp_path, _round(), [_answer(pick=index)])
    assert got["answers"][0]["pick"] == letter


@pytest.mark.parametrize(("rejected", "letters"), [([2, 0, 2], ["A", "C"]), ([], [])])
def test_not_comes_out_sorted_and_once_each(tmp_path, rejected, letters):
    got = _reply(tmp_path, _round(), [_answer(not_=rejected)])
    assert got["answers"][0]["not"] == letters


@pytest.mark.parametrize("none", [True, False])
def test_none_comes_back_as_given(tmp_path, none):
    got = _reply(tmp_path, _round(), [_answer(none=none)])
    assert got["answers"][0]["none"] is none


def test_note_comes_back_with_its_line_breaks(tmp_path):
    got = _reply(tmp_path, _round(), [_answer(note="first\nsecond\n\nthird")])
    assert got["answers"][0]["note"] == "first\nsecond\n\nthird"


def test_questions_come_in_round_order_with_prompts_and_title(tmp_path):
    data = _round(prompts=("One", "Two", "Three"), title="My pad")
    got = _reply(tmp_path, data, [_answer(pick=0), _answer(pick=1), _answer(pick=2)])
    assert got["pad"] == "My pad"
    assert [(a["question"], a["pick"]) for a in got["answers"]] == [("One", "A"), ("Two", "B"), ("Three", "C")]


def test_hostile_prompt_title_and_note_round_trip_unchanged(tmp_path):
    data = _round(prompts=(HOSTILE_TEXT,), title=HOSTILE_TEXT)
    got = _reply(tmp_path, data, [_answer(pick=1, note=HOSTILE_TEXT)])
    assert got["pad"] == HOSTILE_TEXT
    assert got["answers"][0]["question"] == HOSTILE_TEXT
    assert got["answers"][0]["note"] == HOSTILE_TEXT


def test_control_characters_and_a_long_note_round_trip_with_fences_intact(tmp_path):
    text = _text(tmp_path, _round(), [_answer(note=CONTROL_NOTE)])
    assert _body(text)["answers"][0]["note"] == CONTROL_NOTE


def test_reply_is_a_json_fence_with_two_space_indents(tmp_path):
    lines = _text(tmp_path, _round(), [_answer(pick=0)]).split("\n")
    assert lines[0] == "```json"
    assert lines[-1] == "```"
    assert lines[1] == "{"
    assert lines[2] == '  "pad": "Settings page",'
    assert lines[3] == '  "answers": ['
    assert lines[4] == "    {"
    assert lines[5].startswith('      "question"')


@pytest.mark.parametrize(("index", "letter"), [(0, "A"), (25, "Z")])
def test_the_letter_function_maps_an_index_to_its_letter(tmp_path, index, letter):
    assert _probe(tmp_path, _round(), f"return window.PadReply.letter({index});") == letter
