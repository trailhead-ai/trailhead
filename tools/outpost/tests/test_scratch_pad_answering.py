"""Behavioural tests for the scratch-pad kit's answering surface.

Every test builds a real pad with ``scratch_pad.build_pad`` from the real kit,
opens it from ``file://`` through the probe harness, drives the page the way a
user would (button clicks, typing into the note box) and reads the result back
from the DOM.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import chromium_harness as ch

SKILL_DIR = Path(__file__).parent.parent / "plugins" / "outpost" / "skills" / "scratch-pad"

_spec = importlib.util.spec_from_file_location("scratch_pad", SKILL_DIR / "scratch_pad.py")
scratch_pad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scratch_pad)

HELPERS = """
const settle = () => new Promise((r) => setTimeout(r, 0));
const panel = (q) => [...document.querySelectorAll('.pad-question')][q];
const cardOf = (q, o) => panel(q).querySelectorAll('.pad-card')[o];
const btn = (parent, label) => [...parent.querySelectorAll('button')].find((b) => b.textContent === label);
const thisOne = (q, o) => btn(cardOf(q, o), 'This one');
const notThis = (q, o) => btn(cardOf(q, o), 'Not this');
const none = (q) => btn(panel(q), 'None of these');
const note = (q) => panel(q).querySelector('textarea');
const press = async (b) => { b.click(); await settle(); };
const typeNote = async (q, value) => {
  const box = note(q);
  box.value = value;
  box.dispatchEvent(new Event('input', {bubbles: true}));
  await settle();
};
const show = async (q) => { document.querySelectorAll('nav button')[q].click(); await settle(); };
const pressed = (b) => b.getAttribute('aria-pressed');
const mark = (q, o) => {
  const m = cardOf(q, o).querySelector('.pad-mark');
  return m && m.getClientRects().length > 0 ? m.textContent : null;
};
const dot = (q) => {
  const d = document.querySelectorAll('nav button')[q].querySelector('.pad-dot');
  return {answered: d.getAttribute('data-answered'), text: d.textContent};
};
const marks = (q) => [0, 1, 2].map((o) => mark(q, o));
const flags = (q, which) => [0, 1, 2].map((o) => pressed(which(q, o)));
"""


def _round(n_questions=1, picks=(0,), n_options=3):
    return {
        "title": "Settings page",
        "intro": "Where should the controls live?",
        "questions": [
            {
                "prompt": f"Q{q}",
                "options": [
                    {
                        "name": f"Opt {chr(65 + i)}",
                        "cost": f"cost {i}",
                        "pick": i == picks[q % len(picks)],
                        "layout": {"rule": True},
                    }
                    for i in range(n_options)
                ],
            }
            for q in range(n_questions)
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
    return ch.run_probe(pad / "round-1", HELPERS + js)


def test_fresh_load_is_unpressed_hollow_and_empty_whatever_the_session_picked(tmp_path):
    js = """
    const out = [];
    for (let q = 0; q < 2; q++) {
      await show(q);
      out.push({
        this: flags(q, thisOne), not: flags(q, notThis), none: pressed(none(q)),
        note: note(q).value, marks: marks(q), dot: dot(q),
        tags: [...panel(q).querySelectorAll('.pad-card')].map((c) => !!c.querySelector('.pad-pick')),
      });
    }
    return out;
    """
    got = _probe(tmp_path, _round(2, picks=(0, 2)), js)
    for q, tags in enumerate([[True, False, False], [False, False, True]]):
        assert got[q]["tags"] == tags
        assert got[q]["this"] == ["false"] * 3
        assert got[q]["not"] == ["false"] * 3
        assert got[q]["none"] == "false"
        assert got[q]["note"] == ""
        assert got[q]["marks"] == [None] * 3
        assert got[q]["dot"] == {"answered": "false", "text": "not answered"}


def test_pick_moves_to_another_option_and_clears_on_the_same_one(tmp_path):
    js = """
    const seen = [];
    const snap = () => seen.push({this: flags(0, thisOne), marks: marks(0), dot: dot(0)});
    await press(thisOne(0, 1)); snap();
    await press(thisOne(0, 2)); snap();
    await press(thisOne(0, 2)); snap();
    return seen;
    """
    got = _probe(tmp_path, _round(), js)
    assert got[0] == {
        "this": ["false", "true", "false"],
        "marks": [None, "picked", None],
        "dot": {"answered": "true", "text": "answered"},
    }
    assert got[1] == {
        "this": ["false", "false", "true"],
        "marks": [None, None, "picked"],
        "dot": {"answered": "true", "text": "answered"},
    }
    assert got[2] == {"this": ["false"] * 3, "marks": [None] * 3, "dot": {"answered": "false", "text": "not answered"}}


def test_not_this_toggles_per_option_and_picked_option_loses_its_pick(tmp_path):
    js = """
    const seen = [];
    const snap = () => seen.push({
      not: flags(0, notThis), this: flags(0, thisOne), marks: marks(0), dot: dot(0).answered,
    });
    await press(notThis(0, 0)); await press(notThis(0, 2)); snap();
    await press(notThis(0, 0)); snap();
    await press(thisOne(0, 1)); await press(notThis(0, 1)); snap();
    return seen;
    """
    got = _probe(tmp_path, _round(), js)
    assert got[0] == {
        "not": ["true", "false", "true"],
        "this": ["false"] * 3,
        "marks": ["not this", None, "not this"],
        "dot": "false",
    }
    assert got[1] == {
        "not": ["false", "false", "true"],
        "this": ["false"] * 3,
        "marks": [None, None, "not this"],
        "dot": "false",
    }
    assert got[2] == {
        "not": ["false", "true", "true"],
        "this": ["false"] * 3,
        "marks": [None, "not this", "not this"],
        "dot": "false",
    }


def test_picking_an_option_marked_not_this_clears_its_reject(tmp_path):
    js = """
    await press(notThis(0, 0)); await press(notThis(0, 1));
    await press(thisOne(0, 0));
    return {not: flags(0, notThis), this: flags(0, thisOne), marks: marks(0)};
    """
    got = _probe(tmp_path, _round(), js)
    assert got == {
        "not": ["false", "true", "false"],
        "this": ["true", "false", "false"],
        "marks": ["picked", "not this", None],
    }


def test_none_of_these_clears_the_pick_keeps_rejects_and_is_cleared_by_a_pick(tmp_path):
    js = """
    const seen = [];
    await press(notThis(0, 2)); await press(thisOne(0, 0));
    await press(none(0));
    seen.push({none: pressed(none(0)), this: flags(0, thisOne), not: flags(0, notThis), dot: dot(0).answered});
    await press(thisOne(0, 1));
    seen.push({none: pressed(none(0)), this: flags(0, thisOne), dot: dot(0).answered});
    await press(none(0)); await press(none(0));
    seen.push({none: pressed(none(0)), dot: dot(0).answered});
    return seen;
    """
    got = _probe(tmp_path, _round(), js)
    assert got[0] == {"none": "true", "this": ["false"] * 3, "not": ["false", "false", "true"], "dot": "true"}
    assert got[1] == {"none": "false", "this": ["false", "true", "false"], "dot": "true"}
    assert got[2] == {"none": "false", "dot": "false"}


def test_none_of_these_alone_hollows_again_when_turned_off(tmp_path):
    js = """
    await press(none(0)); const on = dot(0).answered;
    await press(none(0));
    return {on, off: dot(0).answered};
    """
    assert _probe(tmp_path, _round(), js) == {"on": "true", "off": "false"}


def test_a_note_is_kept_as_typed_creates_no_element_and_does_not_answer(tmp_path):
    js = """
    const typed = 'line one\\n<b>x</b> "q"';
    const before = document.querySelectorAll('b').length;
    await typeNote(0, typed);
    return {value: note(0).value, typed, bolds: document.querySelectorAll('b').length - before, dot: dot(0).answered};
    """
    got = _probe(tmp_path, _round(), js)
    assert got["value"] == got["typed"] == 'line one\n<b>x</b> "q"'
    assert got["bolds"] == 0
    assert got["dot"] == "false"


def test_note_box_has_a_label_naming_it(tmp_path):
    js = """
    const box = note(0);
    const label = document.querySelector('label[for="' + box.id + '"]');
    return {tag: box.tagName, label: label ? label.textContent : null, id: !!box.id};
    """
    got = _probe(tmp_path, _round(2), js)
    assert got["tag"] == "TEXTAREA"
    assert got["id"]
    assert got["label"]


def test_answers_survive_switching_questions_and_do_not_leak_between_them(tmp_path):
    js = """
    await press(thisOne(0, 1)); await press(notThis(0, 2)); await typeNote(0, 'kept');
    await show(1);
    const q2fresh = {dot: dot(1).answered, this: flags(1, thisOne)};
    await press(none(1));
    await show(0);
    const back = {
      this: flags(0, thisOne), not: flags(0, notThis), note: note(0).value,
      dot0: dot(0).answered, dot1: dot(1).answered, marks: marks(0),
    };
    return {q2fresh, back};
    """
    got = _probe(tmp_path, _round(2), js)
    assert got["q2fresh"] == {"dot": "false", "this": ["false"] * 3}
    assert got["back"] == {
        "this": ["false", "true", "false"],
        "not": ["false", "false", "true"],
        "note": "kept",
        "dot0": "true",
        "dot1": "true",
        "marks": [None, "picked", "not this"],
    }


def test_answering_one_question_leaves_the_other_dot_alone(tmp_path):
    js = """
    await press(thisOne(0, 0));
    return [dot(0).answered, dot(1).answered];
    """
    assert _probe(tmp_path, _round(2), js) == ["true", "false"]


def test_buttons_are_named_for_their_option_across_a_three_option_question(tmp_path):
    js = """
    return [0, 1, 2].map((o) => [thisOne(0, o), notThis(0, o)].map((b) => b.getAttribute('aria-label')));
    """
    got = _probe(tmp_path, _round(), js)
    assert got == [
        ["This one: A, Opt A", "Not this: A, Opt A"],
        ["This one: B, Opt B", "Not this: B, Opt B"],
        ["This one: C, Opt C", "Not this: C, Opt C"],
    ]


def test_entry_label_stays_the_prompt_beside_its_own_dot(tmp_path):
    js = """
    const b = document.querySelectorAll('nav button')[0];
    const label = b.querySelector('.pad-entry-label');
    const d = b.querySelector('.pad-dot');
    return {label: label.textContent, dotInLabel: label.contains(d), tag: b.tagName};
    """
    assert _probe(tmp_path, _round(), js) == {"label": "Q0", "dotInLabel": False, "tag": "BUTTON"}


def test_change_hook_fires_once_per_answer_change_with_the_question_index(tmp_path):
    js = """
    const calls = [];
    window.PadAnswers.onChange((q) => calls.push(q));
    await show(1);
    await press(thisOne(1, 0));
    await press(notThis(0, 1));
    await press(none(1));
    await typeNote(0, 'x');
    return {calls, answers: window.PadAnswers.answers};
    """
    got = _probe(tmp_path, _round(2), js)
    assert got["calls"] == [1, 0, 1, 0]
    assert got["answers"] == [
        {"pick": None, "not": [1], "none": False, "note": "x"},
        {"pick": None, "not": [], "none": True, "note": ""},
    ]
