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

import pytest

import chromium_harness as ch

SKILL_DIR = Path(__file__).parent.parent / "plugins" / "outpost" / "skills" / "scratch-pad"

_spec = importlib.util.spec_from_file_location("scratch_pad", SKILL_DIR / "scratch_pad.py")
scratch_pad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scratch_pad)


@pytest.fixture(autouse=True)
def _outside_any_workspace(tmp_path, monkeypatch):
    """Run from a folder that is in no camp workspace, so the pad builds on disk and nothing is published."""
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)


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


def _probe(tmp_path, data, js, without=()):
    env = {
        "OUTPOST_STATE_DIR": str(tmp_path / "state"),
        "XDG_STATE_HOME": str(tmp_path / "xdg"),
        "HOME": str(tmp_path / "home"),
    }
    round_path = tmp_path / "round.json"
    round_path.write_text(json.dumps(data))
    _, pad = scratch_pad.build_pad(round_path, "demo", env=env)
    for name in without:
        (pad / "round-1" / name).unlink()
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


REPLY_HELPERS = """
const later = () => new Promise((r) => setTimeout(r, 50));
const seen = (e) => !!e && e.getClientRects().length > 0;
const entries = () => [...document.querySelectorAll('nav button')];
const replyEntry = () => entries()[entries().length - 1];
const openReply = async () => { replyEntry().click(); await settle(); };
const replyPanel = () => document.getElementById('pad-reply');
const replyBox = () => replyPanel().querySelector('textarea');
const statusLine = () => replyPanel().querySelector('[role="status"]');
const statusText = () => (seen(statusLine()) ? statusLine().textContent : '');
const copyButton = () => btn(replyPanel(), 'Copy reply');
const copy = async () => { copyButton().click(); await later(); };
const summaries = () => [...replyPanel().querySelectorAll('li')].map((li) => li.textContent);
const unanswered = () => {
  const e = replyPanel().querySelector('.pad-reply-unanswered');
  return seen(e) ? e.textContent : null;
};
const parsed = () => JSON.parse(replyBox().value.replace(/^```json\\n/, '').replace(/\\n```$/, ''));
const useClipboard = (value) => Object.defineProperty(navigator, 'clipboard', {value, configurable: true});
const written = [];
const resolving = () => useClipboard({writeText: (t) => { written.push(t); return Promise.resolve(); }});
const rejecting = () => useClipboard({writeText: () => Promise.reject(new DOMException('no', 'NotAllowedError'))});
const selection = () => ({
  active: document.activeElement === replyBox(),
  start: replyBox().selectionStart,
  end: replyBox().selectionEnd,
  length: replyBox().value.length,
});
"""

COPIED = "Copied. Paste it into your session."
BLOCKED = "Your browser blocked copying. The reply is selected \u2014 press Ctrl+C (\u2318C on a Mac)."


def _reply_probe(tmp_path, data, js, without=()):
    return _probe(tmp_path, data, REPLY_HELPERS + js, without=without)


def test_reply_entry_is_last_dotless_and_shows_its_own_panel_in_place_of_the_question(tmp_path):
    js = """
    const before = entries().length;
    await openReply();
    const onReply = {
      current: entries().map((b) => b.getAttribute('aria-current')),
      dot: !!replyEntry().querySelector('.pad-dot'),
      label: replyEntry().textContent,
      replyShown: seen(replyPanel()),
      questionShown: [...document.querySelectorAll('.pad-question')].some(seen),
      questionPanels: document.querySelectorAll('.pad-question').length,
    };
    await show(1);
    const back = {
      current: entries().map((b) => b.getAttribute('aria-current')),
      replyShown: seen(replyPanel()),
      questionShown: seen(panel(1)),
    };
    return {before, onReply, back};
    """
    got = _reply_probe(tmp_path, _round(2), js)
    assert got["before"] == 3
    assert got["onReply"] == {
        "current": [None, None, "true"],
        "dot": False,
        "label": "Your reply",
        "replyShown": True,
        "questionShown": False,
        "questionPanels": 1,
    }
    assert got["back"] == {"current": [None, "true", None], "replyShown": False, "questionShown": True}


def test_reply_box_holds_the_reply_for_the_answers_and_the_summary_names_each_question(tmp_path):
    js = """
    await press(thisOne(0, 1)); await typeNote(0, 'because');
    await show(1); await press(none(1)); await press(notThis(1, 0));
    await openReply();
    return {reply: parsed(), summaries: summaries(), unanswered: unanswered()};
    """
    got = _reply_probe(tmp_path, _round(3), js)
    assert got["reply"] == {
        "pad": "Settings page",
        "answers": [
            {"question": "Q0", "pick": "B", "not": [], "none": False, "note": "because"},
            {"question": "Q1", "pick": None, "not": ["A"], "none": True, "note": ""},
            {"question": "Q2", "pick": None, "not": [], "none": False, "note": ""},
        ],
    }
    assert got["summaries"] == ["Q0 \u2014 B, Opt B", "Q1 \u2014 none of these", "Q2 \u2014 no answer yet"]
    assert got["unanswered"] == "No pick yet on 1 of 3 questions. You can still copy the reply."


def test_a_question_with_rejects_and_a_note_but_no_pick_reads_no_pick_yet(tmp_path):
    js = """
    await press(notThis(0, 0)); await press(notThis(0, 2)); await typeNote(0, 'hm');
    await show(1); await typeNote(1, 'just a note');
    await show(2); await press(notThis(2, 1));
    await openReply();
    return summaries();
    """
    got = _reply_probe(tmp_path, _round(4), js)
    assert got == [
        "Q0 \u2014 no pick yet (not A, C; has a note)",
        "Q1 \u2014 no pick yet (has a note)",
        "Q2 \u2014 no pick yet (not B)",
        "Q3 \u2014 no answer yet",
    ]


def test_no_unanswered_line_when_every_question_has_a_pick_or_none_and_every_question_when_none_do(tmp_path):
    js = """
    await openReply();
    const none_ = {line: unanswered()};
    await show(0); await press(thisOne(0, 0));
    await show(1); await press(none(1));
    await openReply();
    return {none_, all: unanswered()};
    """
    got = _reply_probe(tmp_path, _round(2), js)
    assert got["none_"]["line"] == "No pick yet on 2 of 2 questions. You can still copy the reply."
    assert got["all"] is None


def test_unanswered_line_counts_one_question_in_singular(tmp_path):
    got = _reply_probe(tmp_path, _round(), "await openReply(); return unanswered();")
    assert got == "No pick yet on 1 of 1 question. You can still copy the reply."


def test_changing_an_answer_after_visiting_the_reply_changes_the_reply(tmp_path):
    js = """
    await openReply();
    const first = parsed().answers[0].pick;
    await show(0); await press(thisOne(0, 2));
    await openReply();
    const second = parsed().answers[0].pick;
    return {first, second, summary: summaries()[0], unanswered: unanswered()};
    """
    got = _reply_probe(tmp_path, _round(), js)
    assert got == {"first": None, "second": "C", "summary": "Q0 \u2014 C, Opt C", "unanswered": None}


def test_a_resolving_clipboard_gets_exactly_the_reply_and_an_answer_change_clears_the_message(tmp_path):
    js = """
    resolving();
    await press(thisOne(0, 0));
    await openReply();
    await copy();
    const after = {written: [...written], box: replyBox().value, status: statusText()};
    await show(0); await press(thisOne(0, 1));
    await openReply();
    return {after, cleared: statusText()};
    """
    got = _reply_probe(tmp_path, _round(), js)
    assert len(got["after"]["written"]) == 1
    assert got["after"]["written"][0] == got["after"]["box"]
    assert '"pick": "A"' in got["after"]["written"][0]
    assert got["after"]["status"] == COPIED
    assert got["cleared"] == ""


@pytest.mark.parametrize("clipboard", ["rejecting()", "useClipboard(undefined)"])
def test_a_blocked_clipboard_selects_the_whole_reply_and_says_so(tmp_path, clipboard):
    js = f"""
    {clipboard};
    await press(thisOne(0, 0)); await typeNote(0, 'n');
    await openReply();
    await copy();
    const out = {{status: statusText(), selection: selection()}};
    await show(0); await press(thisOne(0, 1));
    await openReply();
    out.cleared = statusText();
    return out;
    """
    got = _reply_probe(tmp_path, _round(), js)
    assert got["status"] == BLOCKED
    assert got["selection"]["active"]
    assert got["selection"]["length"] > 0
    assert (got["selection"]["start"], got["selection"]["end"]) == (0, got["selection"]["length"])
    assert got["cleared"] == ""


def test_a_blocked_clipboard_focuses_the_reply_box_without_relying_on_select(tmp_path):
    js = """
    HTMLTextAreaElement.prototype.select = () => {};
    rejecting();
    await openReply();
    await copy();
    return {status: statusText(), selection: selection()};
    """
    got = _reply_probe(tmp_path, _round(), js)
    assert got["status"] == BLOCKED
    assert got["selection"]["active"]
    assert (got["selection"]["start"], got["selection"]["end"]) == (0, got["selection"]["length"])


def test_seam_real_clipboard_lands_in_one_outcome(tmp_path):  # inert-gate: allow seam smoke, real clipboard
    """Drive the page's real clipboard. The headless shell refuses a scripted
    write, so this exercises the fallback; the success path is covered by the
    resolving stub only."""
    js = """
    await openReply();
    await copy();
    const s = selection();
    return {status: statusText(), selected: s.active && s.start === 0 && s.end === s.length && s.length > 0};
    """
    got = _reply_probe(tmp_path, _round(), js)
    assert (got["status"], got["selected"]) in [(COPIED, False), (BLOCKED, True)]


@pytest.mark.parametrize("setup,expected", [("resolving()", COPIED), ("rejecting()", BLOCKED), ("", "")])
def test_status_line_is_a_status_region_and_the_reply_box_is_named_your_reply(tmp_path, setup, expected):
    js = f"""
    {setup};
    await openReply();
    if ({'true' if setup else 'false'}) await copy();
    const box = replyBox();
    const labelled = box.labels && box.labels.length ? box.labels[0].textContent : box.getAttribute('aria-label');
    return {{
      status: statusText(), role: statusLine().getAttribute('role'), label: labelled,
      readOnly: box.readOnly, tag: box.tagName,
    }};
    """
    got = _reply_probe(tmp_path, _round(), js)
    assert got == {"status": expected, "role": "status", "label": "Your reply", "readOnly": True, "tag": "TEXTAREA"}


def test_without_padreply_the_reply_panel_alone_says_it_could_not_be_built(tmp_path):
    js = """
    await press(thisOne(0, 1));
    await show(1); await press(none(1));
    delete window.PadReply;
    await openReply();
    const lines = [...replyPanel().querySelectorAll('p')].filter(seen).map((p) => p.textContent);
    const out = {
      lines, box: seen(replyBox()), copy: seen(copyButton()), summaries: summaries().filter(Boolean).length,
      list: seen(replyPanel().querySelector('ul')),
    };
    await show(0);
    out.entries = entries().slice(0, 2).map((b) => b.querySelector('.pad-entry-label').textContent);
    out.answers = window.PadAnswers.answers;
    out.pressed = flags(0, thisOne);
    out.questionShown = seen(panel(0));
    return out;
    """
    got = _reply_probe(tmp_path, _round(2), js)
    assert got["lines"] == ["The reply could not be built. Answer in words in your session instead."]
    assert not got["box"] and not got["copy"] and not got["list"]
    assert got["entries"] == ["Q0", "Q1"]
    assert got["answers"][0]["pick"] == 1 and got["answers"][1]["none"] is True
    assert got["pressed"] == ["false", "true", "false"]
    assert got["questionShown"]


def test_a_build_that_throws_is_the_same_could_not_be_built_line(tmp_path):
    js = """
    window.PadReply.build = () => { throw new Error('boom'); };
    await openReply();
    const lines = [...replyPanel().querySelectorAll('p')].filter(seen).map((p) => p.textContent);
    await show(0);
    return {lines, shown: seen(panel(0)), failure: !!document.getElementById('pad-failure')};
    """
    got = _reply_probe(tmp_path, _round(), js)
    assert got["lines"] == ["The reply could not be built. Answer in words in your session instead."]
    assert got["shown"] and not got["failure"]


def test_the_reply_shows_hostile_text_as_text(tmp_path):
    data = _round(1)
    data["title"] = "<b>t</b>"
    data["questions"][0]["prompt"] = "<i>p</i>"
    js = """
    const before = document.querySelectorAll('b, i').length;
    await openReply();
    return {extra: document.querySelectorAll('b, i').length - before, summary: summaries()[0], value: replyBox().value};
    """
    got = _reply_probe(tmp_path, data, js)
    assert got["extra"] == 0
    assert got["summary"] == "<i>p</i> \u2014 no answer yet"
    assert '"pad": "<b>t</b>"' in got["value"]


def test_an_answer_change_while_the_reply_is_showing_rebuilds_the_reply(tmp_path):
    js = """
    resolving();
    await openReply();
    await copy();
    const before = {pick: parsed().answers[0].pick, status: statusText()};
    thisOne(0, 2).click();
    await settle();
    return {before, pick: parsed().answers[0].pick, summary: summaries()[0], status: statusText()};
    """
    got = _reply_probe(tmp_path, _round(), js)
    assert got["before"] == {"pick": None, "status": COPIED}
    assert (got["pick"], got["summary"], got["status"]) == ("C", "Q0 — C, Opt C", "")


NO_PLACEHOLDER_JS = """
const letters = [...panel(0).querySelectorAll('.pad-letter')].map((e) => e.textContent);
const names = [...panel(0).querySelectorAll('.pad-card .pad-toggle')].map((b) => b.getAttribute('aria-label'));
await press(thisOne(0, 1));
await show(1);
const shown1 = seen(panel(1));
await openReply();
const lines = [...replyPanel().querySelectorAll('p')].filter(seen).map((p) => p.textContent);
return {
  letters, names, failure: !!document.getElementById('pad-failure'),
  cards: document.querySelectorAll('.pad-card').length,
  pressed: (await show(0), flags(0, thisOne)), dot: dot(0), shown1, lines,
};
"""


@pytest.mark.parametrize("without,reply_lines", [
    (("reply.js",), ["The reply could not be built. Answer in words in your session instead."]),
    ((), []),
])
def test_a_pad_whose_reply_script_did_not_load_still_draws_and_answers(tmp_path, without, reply_lines):
    got = _reply_probe(tmp_path, _round(2), NO_PLACEHOLDER_JS, without=without)
    assert not got["failure"]
    assert got["cards"] == 6
    assert got["letters"] == ["A", "B", "C"]
    assert got["names"] == [
        "This one: A, Opt A", "Not this: A, Opt A",
        "This one: B, Opt B", "Not this: B, Opt B",
        "This one: C, Opt C", "Not this: C, Opt C",
    ]
    assert got["pressed"] == ["false", "true", "false"]
    assert got["dot"] == {"answered": "true", "text": "answered"}
    assert got["shown1"]
    unbuilt = [line for line in got["lines"] if line.startswith("The reply could not")]
    assert unbuilt == reply_lines


def test_the_reply_entry_is_dashed_and_a_question_entry_is_not(tmp_path):
    js = """
    const style = (b) => getComputedStyle(b).borderTopStyle;
    return {reply: style(replyEntry()), question: style(entries()[0])};
    """
    got = _reply_probe(tmp_path, _round(2), js)
    assert got == {"reply": "dashed", "question": "solid"}
