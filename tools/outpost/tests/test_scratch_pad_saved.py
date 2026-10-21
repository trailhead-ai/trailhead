"""Behavioural tests for the scratch-pad kit keeping a round's answers in the browser.

Every test builds a real pad with ``scratch_pad.build_pad`` from the real kit and drives it in the
real headless browser across reloads and between pages (``run_probe_steps``), from ``file://`` and,
where it matters, served under the sites policy. Each browser launch has a fresh profile, so a test
that needs answers to meet a pad built later stages the pads in one folder and moves between them.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

import chromium_harness as ch
from test_scratch_pad_answering import HELPERS, REPLY_HELPERS, _round

SKILL_DIR = Path(__file__).parent.parent / "plugins" / "outpost" / "skills" / "scratch-pad"

_spec = importlib.util.spec_from_file_location("scratch_pad", SKILL_DIR / "scratch_pad.py")
scratch_pad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scratch_pad)

KEY = "outpost-scratch-pad:"
CANT_KEEP = (
    "This browser can't keep your answers, so they will be gone if you reload the page. "
    "Copy your reply before you leave."
)
ROUND1 = "round-1/index.html"
BUDGET = 8000


@pytest.fixture(autouse=True)
def _outside_any_workspace(tmp_path, monkeypatch):
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)


def _env(tmp_path):
    return {
        "OUTPOST_STATE_DIR": str(tmp_path / "state"),
        "XDG_STATE_HOME": str(tmp_path / "xdg"),
        "HOME": str(tmp_path / "home"),
    }


def _build(tmp_path, name="demo", data=None, add=False):
    round_path = tmp_path / f"round-{name}-{'add' if add else 'new'}.json"
    round_path.write_text(json.dumps(data or _round(2)))
    _, pad = scratch_pad.build_pad(round_path, name, env=_env(tmp_path), add=add)
    return pad


def _stage(tmp_path, pad, label):
    """Copy a built pad into ``tmp_path/stage/<label>`` so pads built at different times share one page folder."""
    target = tmp_path / "stage" / label
    shutil.copytree(pad, target)
    return tmp_path / "stage"


ANSWER_JS = """
const answerBoth = async () => {
  await show(0);
  await press(thisOne(0, 1)); await press(notThis(0, 0)); await typeNote(0, 'Bigger buttons');
  await show(1);
  await press(none(1)); await typeNote(1, 'Neither fits');
};
const snapshot = async () => {
  const out = {questions: []};
  for (let q = 0; q < 2; q++) {
    await show(q);
    out.questions.push({
      this: flags(q, thisOne), not: flags(q, notThis), none: pressed(none(q)),
      note: note(q).value, marks: marks(q), dot: dot(q),
    });
  }
  await openReply();
  out.reply = document.getElementById('pad-reply-box').value;
  return out;
};
const BLANK = {this: ['false', 'false', 'false'], not: ['false', 'false', 'false'], none: 'false', note: '',
  marks: [null, null, null], dot: {answered: 'false', text: 'not answered'}};
const blankNow = async (count) => {
  const out = [];
  for (let q = 0; q < count; q++) {
    await show(q);
    out.push({
      this: flags(q, thisOne), not: flags(q, notThis), none: pressed(none(q)),
      note: note(q).value, marks: marks(q), dot: dot(q),
    });
  }
  return out;
};
const roundId = () => window.PAD_ROUND_ID;
const storedKeys = () => Object.keys(localStorage).filter((k) => k.startsWith('outpost-scratch-pad:'));
"""

PRE = HELPERS + REPLY_HELPERS + ANSWER_JS

ANSWERED_Q0 = {
    "this": ["false", "true", "false"],
    "not": ["true", "false", "false"],
    "none": "false",
    "note": "Bigger buttons",
    "marks": ["not this", "picked", None],
    "dot": {"answered": "true", "text": "answered"},
}
ANSWERED_Q1 = {
    "this": ["false", "false", "false"],
    "not": ["false", "false", "false"],
    "none": "true",
    "note": "Neither fits",
    "marks": [None, None, None],
    "dot": {"answered": "true", "text": "answered"},
}
BLANK_Q = {
    "this": ["false"] * 3,
    "not": ["false"] * 3,
    "none": "false",
    "note": "",
    "marks": [None] * 3,
    "dot": {"answered": "false", "text": "not answered"},
}


def _steps(pad_dir, steps, **kw):
    kw.setdefault("virtual_time_budget", BUDGET)
    return ch.run_probe_steps(pad_dir, [PRE + s for s in steps], **kw)


@pytest.mark.parametrize("serve", [False, True], ids=["file", "served"])
def test_every_answer_and_the_reply_come_back_after_a_reload(tmp_path, serve):
    pad = _build(tmp_path)
    got = _steps(
        pad,
        [
            """
            await answerBoth();
            sessionStorage.setItem('before', JSON.stringify(await snapshot()));
            harness.reload();
            """,
            """
            return {before: JSON.parse(sessionStorage.getItem('before')), after: await snapshot()};
            """,
        ],
        entry=ROUND1,
        inject_into=ROUND1,
        serve=serve,
    )
    assert got["after"] == got["before"]
    assert got["after"]["questions"] == [ANSWERED_Q0, ANSWERED_Q1]
    assert '"pick": "B"' in got["after"]["reply"]
    assert '"note": "Neither fits"' in got["after"]["reply"]


def test_the_restored_dots_already_read_answered_when_the_page_is_ready(tmp_path):
    pad = _build(tmp_path)
    got = _steps(
        pad,
        [
            "await answerBoth(); harness.reload();",
            """
            return {
              ready: document.documentElement.hasAttribute('data-pad-ready'),
              dots: [dot(0), dot(1)],
            };
            """,
        ],
        entry=ROUND1,
        inject_into=ROUND1,
    )
    assert got["ready"] is True
    assert got["dots"] == [{"answered": "true", "text": "answered"}] * 2


def test_a_round_with_no_entry_opens_blank_even_when_storage_holds_another_rounds(tmp_path):
    pad = _build(tmp_path)
    got = _steps(
        pad,
        [
            """
            const other = {answers: [
              {pick: 1, not: [], none: false, note: 'elsewhere'}, {pick: null, not: [], none: true, note: ''}]};
            localStorage.setItem('outpost-scratch-pad:' + 'f'.repeat(32), JSON.stringify(other));
            harness.reload();
            """,
            "return {blank: await blankNow(2), keys: storedKeys().length};",
        ],
        entry=ROUND1,
        inject_into=ROUND1,
    )
    assert got["blank"] == [BLANK_Q, BLANK_Q]
    assert got["keys"] == 1


def test_two_rounds_of_one_pad_keep_their_own_answers(tmp_path):
    _build(tmp_path)
    pad = _build(tmp_path, data={**_round(2), "title": "Second"}, add=True)
    got = _steps(
        pad,
        [
            "await show(0); await press(thisOne(0, 1)); harness.goto('../round-2/index.html');",
            """
            const seenBlank = await blankNow(2);
            await show(0); await press(notThis(0, 2)); await typeNote(0, 'second');
            sessionStorage.setItem('blank2', JSON.stringify(seenBlank));
            harness.goto('../round-1/index.html');
            """,
            """
            const one = await blankNow(2);
            sessionStorage.setItem('one', JSON.stringify(one));
            harness.goto('../round-2/index.html');
            """,
            """
            return {
              blank2: JSON.parse(sessionStorage.getItem('blank2')),
              one: JSON.parse(sessionStorage.getItem('one')),
              two: await blankNow(2),
            };
            """,
        ],
        entry=ROUND1,
        inject_into=[ROUND1, "round-2/index.html"],
    )
    assert got["blank2"] == [BLANK_Q, BLANK_Q]
    assert got["one"][0]["this"] == ["false", "true", "false"]
    assert got["one"][0]["not"] == ["false"] * 3
    assert got["one"][0]["note"] == ""
    assert got["two"][0]["not"] == ["false", "false", "true"]
    assert got["two"][0]["this"] == ["false"] * 3
    assert got["two"][0]["note"] == "second"


def test_two_pads_from_disk_with_the_same_title_and_questions_keep_separate_answers(tmp_path):
    first = _build(tmp_path, "alpha")
    second = _build(tmp_path, "beta")
    assert first.parent == second.parent
    got = _steps(
        first.parent,
        [
            "await answerBoth(); harness.goto('../../beta/round-1/index.html');",
            """
            const blank = await blankNow(2);
            await show(0); await press(thisOne(0, 2));
            harness.goto('../../alpha/round-1/index.html');
            sessionStorage.setItem('blank', JSON.stringify(blank));
            """,
            """
            return {blank: JSON.parse(sessionStorage.getItem('blank')), alpha: (await snapshot()).questions};
            """,
        ],
        entry="alpha/round-1/index.html",
        inject_into=["alpha/round-1/index.html", "beta/round-1/index.html"],
    )
    assert got["blank"] == [BLANK_Q, BLANK_Q]
    assert got["alpha"] == [ANSWERED_Q0, ANSWERED_Q1]


def test_a_pad_built_again_under_a_deleted_pads_name_opens_blank(tmp_path):
    old = _build(tmp_path, "demo")
    stage = _stage(tmp_path, old, "old")
    shutil.rmtree(old)
    new = _build(tmp_path, "demo")
    stage = _stage(tmp_path, new, "new")
    got = _steps(
        stage,
        [
            "await answerBoth(); harness.goto('../../new/round-1/index.html');",
            "return {blank: await blankNow(2), kept: storedKeys().length};",
        ],
        entry="old/round-1/index.html",
        inject_into=["old/round-1/index.html", "new/round-1/index.html"],
    )
    assert got["blank"] == [BLANK_Q, BLANK_Q]
    assert got["kept"] == 1


def test_adding_a_round_keeps_the_earlier_rounds_answers_and_opens_the_new_round_blank(tmp_path):
    pad = _build(tmp_path)
    before = _stage(tmp_path, pad, "before")
    _build(tmp_path, data={**_round(2), "title": "Second"}, add=True)
    after = _stage(tmp_path, pad, "after")
    assert before == after
    got = _steps(
        after,
        [
            "await answerBoth(); harness.goto('../../after/round-1/index.html');",
            """
            sessionStorage.setItem('one', JSON.stringify((await snapshot()).questions));
            harness.goto('../round-2/index.html');
            """,
            """
            return {one: JSON.parse(sessionStorage.getItem('one')), two: await blankNow(2)};
            """,
        ],
        entry="before/round-1/index.html",
        inject_into=["before/round-1/index.html", "after/round-1/index.html", "after/round-2/index.html"],
    )
    assert got["one"] == [ANSWERED_Q0, ANSWERED_Q1]
    assert got["two"] == [BLANK_Q, BLANK_Q]


GOOD = (
    "const blank = () => ({pick: null, not: [], none: false, note: ''});"
    " const good = () => [blank(), blank()];"
)

BAD_ENTRIES = {
    "not-json": ("return 'not json {';", "not JSON"),
    "over-the-cap": (
        "return ' '.repeat(200000) + JSON.stringify({answers: good()});",
        "too long",
    ),
    "proto-key": (
        "return '{\"__proto__\": {\"x\": 1}, \"answers\": ' + JSON.stringify(good()) + '}';",
        "top-level",
    ),
    "extra-top-level-key": ("return JSON.stringify({answers: good(), extra: 1});", "top-level"),
    "wrong-answer-count": ("return JSON.stringify({answers: good().slice(0, 1)});", "answer count"),
    "too-many-answers": (
        "const a = good(); a.push(a[0]); return JSON.stringify({answers: a});",
        "answer count",
    ),
    "answer-missing-key": (
        "const a = good(); delete a[1].note; return JSON.stringify({answers: a});",
        "answer keys",
    ),
    "answer-key-swapped-for-another": (
        "const a = good(); delete a[1].note; a[1].other = ''; return JSON.stringify({answers: a});",
        "answer keys",
    ),
    "answer-extra-key": (
        "const a = good(); a[0].extra = 1; return JSON.stringify({answers: a});",
        "answer keys",
    ),
    "pick-out-of-range": (
        "const a = good(); a[0].pick = 3; return JSON.stringify({answers: a});",
        "pick is not null",
    ),
    "pick-fractional": (
        "const a = good(); a[0].pick = 1.5; return JSON.stringify({answers: a});",
        "pick is not null",
    ),
    "pick-string": (
        "const a = good(); a[0].pick = '1'; return JSON.stringify({answers: a});",
        "pick is not null",
    ),
    "not-out-of-range": (
        "const a = good(); a[1].not = [3]; return JSON.stringify({answers: a});",
        "not list",
    ),
    "not-duplicate": (
        "const a = good(); a[1].not = [1, 1]; return JSON.stringify({answers: a});",
        "not list",
    ),
    "not-holds-the-pick": (
        "const a = good(); a[0].pick = 1; a[0].not = [1]; return JSON.stringify({answers: a});",
        "holds the pick",
    ),
    "none-with-a-pick": (
        "const a = good(); a[0].pick = 1; a[0].none = true; return JSON.stringify({answers: a});",
        "none with a pick",
    ),
    "note-not-a-string": (
        "const a = good(); a[1].note = 5; return JSON.stringify({answers: a});",
        "note",
    ),
    "note-too-long": (
        "const a = good(); a[1].note = 'x'.repeat(10001); return JSON.stringify({answers: a});",
        "note",
    ),
}

CAP_SPY = (
    "(function () { var parse = JSON.parse; window.__bigParses = 0;"
    " JSON.parse = function (text) { if (typeof text === 'string' && text.length > 150000) window.__bigParses++;"
    " return parse.apply(this, arguments); };"
    " var warn = console.warn; window.__warns = [];"
    " console.warn = function () { window.__warns.push([].join.call(arguments, ' ')); }; })();"
)


@pytest.mark.parametrize("make,marker", list(BAD_ENTRIES.values()), ids=list(BAD_ENTRIES))
def test_an_entry_that_does_not_fit_the_round_is_ignored_whole_until_the_first_change_replaces_it(
    tmp_path, make, marker
):
    pad = _build(tmp_path)
    got = _steps(
        pad,
        [
            GOOD
            + f"""
            const seed = (() => {{ {make} }})();
            localStorage.setItem('outpost-scratch-pad:' + roundId(), seed);
            sessionStorage.setItem('seed', seed);
            harness.reload();
            """,
            """
            const key = 'outpost-scratch-pad:' + roundId();
            const out = {
              blank: await blankNow(2),
              warns: window.__warns.slice(),
              bigParses: window.__bigParses,
              untouched: localStorage.getItem(key) === sessionStorage.getItem('seed'),
              failure: !!document.getElementById('pad-failure'),
              cantKeep: seen(document.getElementById('pad-cant-keep')),
              id: roundId(),
            };
            await show(0);
            await press(thisOne(0, 1));
            out.marks = marks(0);
            out.stored = JSON.parse(localStorage.getItem(key));
            return out;
            """,
        ],
        entry=ROUND1,
        inject_into=ROUND1,
        before_script=CAP_SPY,
    )
    assert got["blank"] == [BLANK_Q, BLANK_Q]
    assert len(got["warns"]) == 1
    assert got["id"] in got["warns"][0]
    assert marker in got["warns"][0]
    assert got["bigParses"] == 0
    assert got["untouched"] is True
    assert got["failure"] is False
    assert got["cantKeep"] is False
    assert got["marks"] == [None, "picked", None]
    assert got["stored"] == {
        "answers": [
            {"pick": 1, "not": [], "none": False, "note": ""},
            {"pick": None, "not": [], "none": False, "note": ""},
        ]
    }


def test_restored_hostile_note_text_shows_as_characters_in_the_box_and_the_reply(tmp_path):
    pad = _build(tmp_path)
    hostile = "<script>window.pwned=1</script><img src=x onerror=window.pwned=1>"
    got = _steps(
        pad,
        [
            f"""
            const entry = {{answers: [
              {{pick: null, not: [], none: false, note: {json.dumps(hostile)}}},
              {{pick: null, not: [], none: false, note: ''}}]}};
            localStorage.setItem('outpost-scratch-pad:' + roundId(), JSON.stringify(entry));
            harness.reload();
            """,
            """
            await show(0);
            const box = note(0).value;
            await openReply();
            return {
              box, reply: document.getElementById('pad-reply-box').value,
              pwned: window.pwned === undefined ? null : window.pwned,
              injected: document.querySelectorAll('#pad img, #pad script, #pad [onerror]').length,
            };
            """,
        ],
        entry=ROUND1,
        inject_into=ROUND1,
    )
    assert got["box"] == hostile
    assert hostile in got["reply"]
    assert got["pwned"] is None
    assert got["injected"] == 0


LINE = """
const line = () => document.getElementById('pad-cant-keep');
const lineState = () => ({shown: seen(line()), text: line().textContent, role: line().getAttribute('role')});
"""

DENIED = (
    "Object.defineProperty(window, 'localStorage',"
    " {configurable: true, get() { throw new DOMException('denied', 'SecurityError'); }});"
)
FULL = "Storage.prototype.setItem = function () { throw new DOMException('full', 'QuotaExceededError'); };"

WORKS_WITHOUT_SAVING = """
resolving();
await show(0);
await press(thisOne(0, 2)); await press(notThis(0, 0)); await typeNote(0, 'still here');
await openReply();
const reply = parsed().answers[0];
await copy();
return {
  line: lineState(), reply, written: written.length, status: statusText(),
  failure: !!document.getElementById('pad-failure'),
};
"""


def test_when_storage_cannot_be_read_the_line_shows_from_the_start_and_the_pad_still_works(tmp_path):
    pad = _build(tmp_path)
    got = _steps(
        pad,
        [LINE + "const first = lineState(); " + WORKS_WITHOUT_SAVING.replace("return {", "return {first,", 1)],
        entry=ROUND1,
        inject_into=ROUND1,
        before_script=DENIED,
    )
    assert got["first"] == {"shown": True, "text": CANT_KEEP, "role": "status"}
    assert got["line"]["shown"] is True
    assert got["reply"] == {"question": "Q0", "pick": "C", "not": ["A"], "none": False, "note": "still here"}
    assert got["written"] == 1
    assert got["status"] == "Copied. Paste it into your session."
    assert got["failure"] is False


def test_when_only_writing_fails_the_line_is_absent_on_open_and_appears_at_the_first_change(tmp_path):
    pad = _build(tmp_path)
    got = _steps(
        pad,
        [
            LINE
            + """
            const open = lineState();
            await show(0);
            await press(thisOne(0, 2));
            const afterChange = lineState();
            await typeNote(0, 'more');
            await openReply();
            return {open, afterChange, later: lineState(), pick: parsed().answers[0].pick,
              failure: !!document.getElementById('pad-failure')};
            """
        ],
        entry=ROUND1,
        inject_into=ROUND1,
        before_script=FULL,
    )
    assert got["open"]["shown"] is False
    assert got["afterChange"] == {"shown": True, "text": CANT_KEEP, "role": "status"}
    assert got["later"]["shown"] is True
    assert got["pick"] == "C"
    assert got["failure"] is False


def test_a_round_with_no_id_line_shows_the_line_and_still_works(tmp_path):
    pad = _build(tmp_path)
    round_js = pad / "round-1" / "round.js"
    lines = round_js.read_text().splitlines(keepends=True)
    assert lines[2].startswith("window.PAD_ROUND_ID = ")
    round_js.write_text("".join(lines[:2]))
    got = _steps(pad, [LINE + WORKS_WITHOUT_SAVING], entry=ROUND1, inject_into=ROUND1)
    assert got["line"] == {"shown": True, "text": CANT_KEEP, "role": "status"}
    assert got["reply"]["pick"] == "C"
    assert got["failure"] is False


def test_with_working_storage_and_an_id_the_line_is_hidden(tmp_path):
    pad = _build(tmp_path)
    got = _steps(
        pad,
        [LINE + "await show(0); await press(thisOne(0, 1)); return {line: lineState(), exists: !!line()};"],
        entry=ROUND1,
        inject_into=ROUND1,
    )
    assert got["exists"] is True
    assert got["line"]["shown"] is False
    assert got["line"]["role"] == "status"


def test_a_note_of_ten_thousand_characters_is_kept_and_the_box_takes_no_more(tmp_path):
    pad = _build(tmp_path)
    got = _steps(
        pad,
        [
            """
            await show(0);
            await typeNote(0, 'n'.repeat(10000));
            harness.reload();
            """,
            """
            await show(0);
            const box = note(0);
            const kept = box.value.length;
            box.focus();
            box.setSelectionRange(kept, kept);
            document.execCommand('insertText', false, 'extra');
            return {kept, after: box.value.length, max: box.maxLength};
            """,
        ],
        entry=ROUND1,
        inject_into=ROUND1,
    )
    assert got == {"kept": 10000, "after": 10000, "max": 10000}


PAIR_JS = """
const quick = () => [0, 1].map((q) => ({
  this: flags(q, thisOne), not: flags(q, notThis), none: pressed(none(q)), note: note(q).value, dot: dot(q),
}));
const ready = async () => {
  await show(1); await show(0);
  let back = false;
  window.addEventListener('pageshow', () => { back = true; });
  try { await harness.waitFor('the load-time pageshow', () => back, 300); } catch (error) {}
};
const stored = () => JSON.parse(localStorage.getItem('outpost-scratch-pad:' + roundId()));
const entryWith = (pick) => JSON.stringify({answers: [
  {pick, not: [], none: false, note: ''}, {pick: null, not: [], none: false, note: ''}]});
"""


def _pair(pad, one, two, *, serve=False, **kw):
    return ch.run_probe_pair(
        pad, PRE + PAIR_JS + one, PRE + PAIR_JS + two, entry=ROUND1, inject_into=ROUND1, serve=serve, **kw
    )


@pytest.mark.parametrize("serve", [False, True], ids=["file", "served"])
def test_a_pick_in_one_tab_shows_in_the_other_without_a_reload(tmp_path, serve):
    pad = _build(tmp_path)
    _, got = _pair(
        pad,
        """
        await harness.barrier();
        await show(0);
        await press(thisOne(0, 1));
        await harness.barrier();
        """,
        """
        await ready();
        await harness.barrier();
        await harness.waitFor('the pick to arrive', () => pressed(thisOne(0, 1)) === 'true');
        const after = await snapshot();
        await harness.barrier();
        return after;
        """,
        serve=serve,
    )
    first = got["questions"][0]
    assert first["this"] == ["false", "true", "false"]
    assert first["marks"] == [None, "picked", None]
    assert first["dot"] == {"answered": "true", "text": "answered"}
    assert '"pick": "B"' in got["reply"]


def test_a_note_typed_in_the_second_tab_appears_in_the_first(tmp_path):
    pad = _build(tmp_path)
    got, _ = _pair(
        pad,
        """
        await ready();
        await harness.barrier();
        await harness.waitFor('the note to arrive', () => note(0).value === 'from the other tab');
        await harness.barrier();
        return note(0).value;
        """,
        """
        await harness.barrier();
        await show(0);
        await typeNote(0, 'from the other tab');
        await harness.barrier();
        """,
    )
    assert got == "from the other tab"


def test_a_change_on_question_two_is_there_when_the_other_tab_moves_to_it(tmp_path):
    pad = _build(tmp_path)
    _, got = _pair(
        pad,
        """
        await harness.barrier();
        await show(1);
        await press(thisOne(1, 2)); await typeNote(1, 'second');
        await harness.barrier();
        """,
        """
        await show(0);
        await harness.barrier();
        await harness.waitFor('question 2 to read answered', () => dot(1).answered === 'true');
        await show(1);
        const out = {this: flags(1, thisOne), marks: marks(1), note: note(1).value};
        await harness.barrier();
        return out;
        """,
    )
    assert got == {"this": ["false", "false", "true"], "marks": [None, None, "picked"], "note": "second"}


def test_a_pick_in_each_tab_leaves_both_in_the_stored_entry(tmp_path):
    pad = _build(tmp_path)
    got, _ = _pair(
        pad,
        """
        await harness.barrier();
        await show(0);
        await press(thisOne(0, 0));
        await harness.barrier();
        await harness.waitFor('the second tab pick', () => stored().answers[1].pick === 2);
        await harness.barrier();
        return stored().answers.map((a) => a.pick);
        """,
        """
        await ready();
        await harness.barrier();
        await harness.waitFor('the first tab pick', () => pressed(thisOne(0, 0)) === 'true');
        await show(1);
        await press(thisOne(1, 2));
        await harness.barrier();
        await harness.barrier();
        """,
    )
    assert got == [0, 2]


IGNORED = {  # name: (copy 1 action, copy 2 action, key the event carries, console warnings copy 2 gives)
    "bad-data": ("localStorage.setItem('outpost-scratch-pad:' + roundId(), '{not json');", "", "own", 1),
    "fails-the-checks": (
        "localStorage.setItem('outpost-scratch-pad:' + roundId(), entryWith(9));",
        "",
        "own",
        1,
    ),
    "another-round": ("localStorage.setItem('outpost-scratch-pad:other-round', entryWith(0));", "", "other", 0),
    "removed": ("localStorage.removeItem('outpost-scratch-pad:' + roundId());", "", "own", 0),
    "cleared": ("localStorage.clear();", "", "cleared", 0),
    "other-storage-area": (
        "",
        """
        window.dispatchEvent(new StorageEvent('storage', {
          key: 'outpost-scratch-pad:' + roundId(), newValue: entryWith(0), storageArea: sessionStorage}));
        """,
        "own",
        0,
    ),
}


@pytest.mark.parametrize("name", list(IGNORED))
def test_an_event_that_is_not_this_rounds_saved_answers_leaves_the_tab_as_it_was(tmp_path, name):
    action_one, action_two, carries, warnings = IGNORED[name]
    pad = _build(tmp_path)
    _, got = _pair(
        pad,
        f"""
        await harness.barrier();
        await show(0);
        await press(thisOne(0, 1));
        await harness.barrier();
        {action_one}
        localStorage.setItem('sentinel-key', '1');
        await harness.barrier();
        """,
        f"""
        await ready();
        const log = [];
        const warns = [];
        console.warn = (...args) => warns.push(args.join(' '));
        window.addEventListener('storage', (e) => log.push({{key: e.key, state: quick()}}));
        await harness.barrier();
        await harness.waitFor('the first pick', () => pressed(thisOne(0, 1)) === 'true');
        const baseline = quick();
        await harness.barrier();
        {action_two}
        await harness.barrier();
        await harness.waitFor('the sentinel', () => log.some((l) => l.key === 'sentinel-key'));
        return {{baseline, after: quick(), log, id: roundId(), warns}};
        """,
    )
    own = KEY + got["id"]
    carried = {"own": own, "other": KEY + "other-round", "cleared": None}[carries]
    assert [entry["key"] for entry in got["log"]] == [own, carried, "sentinel-key"]
    assert got["baseline"][0]["this"] == ["false", "true", "false"]
    for entry in got["log"][1:]:
        assert entry["state"] == got["baseline"]
    assert got["after"] == got["baseline"]
    assert len(got["warns"]) == warnings


def test_taking_the_other_tabs_answers_writes_nothing_until_this_tab_changes_something(tmp_path):
    pad = _build(tmp_path)
    _, got = _pair(
        pad,
        """
        await harness.barrier();
        await show(0);
        await press(thisOne(0, 1)); await typeNote(0, 'theirs');
        await harness.barrier();
        await harness.barrier();
        """,
        """
        await ready();
        await harness.barrier();
        await harness.waitFor('the other tab', () => note(0).value === 'theirs');
        const adopted = window.__sets;
        await press(thisOne(1, 0));
        const own = window.__sets;
        await harness.barrier();
        await harness.barrier();
        return {adopted, own};
        """,
        before_script="""
        window.__sets = 0;
        const original = Storage.prototype.setItem;
        Storage.prototype.setItem = function () { window.__sets += 1; return original.apply(this, arguments); };
        """,
    )
    assert got == {"adopted": 0, "own": 1}


def test_a_note_box_being_typed_in_keeps_its_text_and_caret_while_the_other_controls_change(tmp_path):
    pad = _build(tmp_path)
    _, got = _pair(
        pad,
        """
        await harness.barrier();
        await show(0);
        await typeNote(0, 'theirs');
        await press(thisOne(0, 2));
        await harness.barrier();
        """,
        """
        await ready();
        await typeNote(0, 'my own words');
        note(0).focus();
        note(0).setSelectionRange(3, 7);
        await harness.barrier();
        await harness.waitFor('the pick to arrive', () => pressed(thisOne(0, 2)) === 'true');
        const out = {
          text: note(0).value, start: note(0).selectionStart, end: note(0).selectionEnd,
          focused: document.activeElement === note(0), picked: flags(0, thisOne), dot: dot(0),
        };
        await harness.barrier();
        return out;
        """,
    )
    assert got == {
        "text": "my own words",
        "start": 3,
        "end": 7,
        "focused": True,
        "picked": ["false", "false", "true"],
        "dot": {"answered": "true", "text": "answered"},
    }


MISSED = {
    "pageshow": ("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));", True),
    "visible": (
        """
        Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'visible'});
        document.dispatchEvent(new Event('visibilitychange'));
        """,
        True,
    ),
    "hidden": (
        """
        Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
        document.dispatchEvent(new Event('visibilitychange'));
        """,
        False,
    ),
}


@pytest.mark.parametrize("name", list(MISSED))
def test_a_tab_that_missed_the_event_reads_its_entry_again_when_it_comes_back_into_view(tmp_path, name):
    event, takes = MISSED[name]
    pad = _build(tmp_path)
    got = ch.run_probe(
        pad,
        PRE
        + PAIR_JS
        + f"""
        await ready();
        localStorage.setItem('outpost-scratch-pad:' + roundId(), entryWith(1));
        const before = quick();
        {event}
        await settle();
        const shown = quick();
        await press(thisOne(1, 2));
        return {{before, shown, pick: stored().answers.map((a) => a.pick)}};
        """,
        entry=ROUND1,
        inject_into=ROUND1,
    )
    assert got["before"][0]["this"] == ["false", "false", "false"]
    if takes:
        assert got["shown"][0]["this"] == ["false", "true", "false"]
        assert got["shown"][0]["dot"]["answered"] == "true"
        assert got["pick"] == [1, 2]
    else:
        assert got["shown"] == got["before"]
        assert got["pick"] == [None, 2]


def test_coming_back_into_view_with_the_entry_unchanged_leaves_the_copied_line_alone(tmp_path):
    pad = _build(tmp_path)
    got = ch.run_probe(
        pad,
        PRE
        + PAIR_JS
        + """
        resolving();
        await ready();
        await press(thisOne(0, 1));
        await openReply();
        await copy();
        const copied = statusText();
        window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
        await settle();
        const unchanged = statusText();
        localStorage.setItem('outpost-scratch-pad:' + roundId(), entryWith(2));
        window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
        await settle();
        return {copied, unchanged, changed: statusText(), reply: parsed().answers[0].pick};
        """,
        entry=ROUND1,
        inject_into=ROUND1,
    )
    assert got == {
        "copied": "Copied. Paste it into your session.",
        "unchanged": "Copied. Paste it into your session.",
        "changed": "",
        "reply": "C",
    }


def test_a_stored_entry_that_was_ignored_at_open_is_not_warned_about_again_when_the_tab_returns(tmp_path):
    pad = _build(tmp_path)
    got = _steps(
        pad,
        [
            """
            localStorage.setItem('outpost-scratch-pad:' + roundId(), '{not json');
            harness.reload();
            """,
            PAIR_JS
            + """
            await ready();
            const warns = [];
            console.warn = (...args) => warns.push(args.join(' '));
            window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
            await settle();
            return {warns, blank: quick()[0].this};
            """,
        ],
        entry=ROUND1,
        inject_into=ROUND1,
    )
    assert got == {"warns": [], "blank": ["false", "false", "false"]}


def test_a_reply_that_is_showing_is_rebuilt_when_the_other_tab_changes_an_answer(tmp_path):
    pad = _build(tmp_path)
    _, got = _pair(
        pad,
        """
        await harness.barrier();
        await show(1);
        await press(none(1));
        await harness.barrier();
        """,
        """
        await ready();
        await openReply();
        await harness.barrier();
        await harness.waitFor('the reply to name the other tab', () => parsed().answers[1].none === true);
        const out = {pick: parsed().answers[1].pick, summary: summaries()[1], shown: seen(replyBox())};
        await harness.barrier();
        return out;
        """,
    )
    assert got["shown"] is True
    assert got["summary"].endswith("none of these")
