"""Behavioural tests for the scratch-pad kit's page.

Every test builds a real pad with ``scratch_pad.build_pad`` from the real kit,
opens the built round folder from ``file://`` in a headless browser through the
probe harness, and asserts on what the page rendered.
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


HOSTILE = [
    "<script>window.pwned=1</script>",
    "<img src=x onerror=window.pwned=1>",
    "<!-- c -->",
]

SNAPSHOT = """
const shown = (e) => e.getClientRects().length > 0;
const text = (e) => (e ? e.textContent : null);
const entries = [...document.querySelectorAll('nav button:not(.pad-reply-entry)')].map((b) => ({
  text: text(b.querySelector('.pad-entry-label')),
  current: b.getAttribute('aria-current'),
}));
const cards = [...document.querySelectorAll('.pad-card')].filter(shown).map((c) => ({
  letter: text(c.querySelector('.pad-letter')),
  name: text(c.querySelector('.pad-name')),
  cost: text(c.querySelector('.pad-cost')),
  pick: !!c.querySelector('.pad-pick'),
  pickText: text(c.querySelector('.pad-pick')),
}));
const loading = document.getElementById('pad-loading');
const failure = document.getElementById('pad-failure');
"""


def _option(name, cost, pick=False):
    return {"name": name, "cost": cost, "pick": pick, "layout": {"rule": True}}


def _question(prompt, n_options=2, pick=0):
    return {
        "prompt": prompt,
        "options": [_option(f"{prompt} opt {i}", f"{prompt} cost {i}", i == pick) for i in range(n_options)],
    }


def _round(n_questions=1, **over):
    data = {
        "title": "Settings page",
        "intro": "Where should the controls live?",
        "questions": [_question(f"Q{i}") for i in range(n_questions)],
    }
    data.update(over)
    return data


def _build(tmp_path, data):
    round_path = tmp_path / "round.json"
    round_path.write_text(json.dumps(data))
    env = {"OUTPOST_STATE_DIR": str(tmp_path / "state")}
    index, pad = scratch_pad.build_pad(round_path, "demo", env=env)
    return pad


def _run(tmp_path, data, js, mutate=None, **kwargs):
    pad = _build(tmp_path, data)
    round_dir = pad / "round-1"
    if mutate:
        mutate(round_dir)
    return ch.run_probe(round_dir, SNAPSHOT + js, **kwargs)


def test_one_question_shows_one_selected_entry_and_its_cards(tmp_path):
    got = _run(tmp_path, _round(1), "return {entries, cards};")
    assert got["entries"] == [{"text": "Q0", "current": "true"}]
    assert [c["name"] for c in got["cards"]] == ["Q0 opt 0", "Q0 opt 1"]


def test_three_questions_list_in_order_and_show_only_the_first(tmp_path):
    got = _run(tmp_path, _round(3), "return {entries, cards};")
    assert [e["text"] for e in got["entries"]] == ["Q0", "Q1", "Q2"]
    assert [e["current"] for e in got["entries"]] == ["true", None, None]
    assert [c["name"] for c in got["cards"]] == ["Q0 opt 0", "Q0 opt 1"]


def test_clicking_an_entry_shows_only_that_question_and_marks_it_current(tmp_path):
    js = """
    document.querySelectorAll('nav button')[2].click();
    await new Promise((r) => setTimeout(r, 0));
    const after = [...document.querySelectorAll('nav button')].map((b) => b.getAttribute('aria-current'));
    const cardsNow = [...document.querySelectorAll('.pad-card')]
      .filter(shown)
      .map((c) => text(c.querySelector('.pad-name')));
    return {after, cardsNow};
    """
    got = _run(tmp_path, _round(3), js)
    assert got["after"] == [None, None, "true", None]
    assert got["cardsNow"] == ["Q2 opt 0", "Q2 opt 1"]


def test_card_letters_names_and_costs_come_back_as_given(tmp_path):
    data = _round(1, questions=[_question("Header", n_options=3)])
    got = _run(tmp_path, data, "return cards;")
    assert [(c["letter"], c["name"], c["cost"]) for c in got] == [
        ("A", "Header opt 0", "Header cost 0"),
        ("B", "Header opt 1", "Header cost 1"),
        ("C", "Header opt 2", "Header cost 2"),
    ]


@pytest.mark.parametrize("pick", [0, 1, 2])
def test_my_pick_tag_is_text_on_exactly_the_picked_option(tmp_path, pick):
    data = _round(1, questions=[_question("Header", n_options=3, pick=pick)])
    got = _run(tmp_path, data, "return cards;")
    assert [c["pick"] for c in got] == [i == pick for i in range(3)]
    assert got[pick]["pickText"] == "my pick"


@pytest.mark.parametrize("payload", HOSTILE)
def test_round_strings_show_as_literal_text(tmp_path, payload):
    data = {
        "title": payload,
        "intro": payload,
        "questions": [
            {
                "prompt": payload,
                "options": [_option(payload, payload, True), _option("other", "cost")],
            }
        ],
    }
    js = """
    const comments = [];
    const walker = document.createTreeWalker(document, NodeFilter.SHOW_COMMENT);
    while (walker.nextNode()) comments.push(walker.currentNode.data);
    return {
      title: text(document.querySelector('h1')),
      intro: text(document.querySelector('.pad-intro')),
      prompt: text(document.querySelector('.pad-prompt')),
      name: cards[0].name,
      cost: cards[0].cost,
      pwned: typeof window.pwned,
      imgs: document.querySelectorAll('img').length,
      inlineScripts: [...document.scripts].filter((s) => !s.hasAttribute('src')).length,
      comments,
    };
    """
    got = _run(tmp_path, data, js)
    assert got["title"] == got["intro"] == got["prompt"] == got["name"] == got["cost"] == payload
    assert got["pwned"] == "undefined"
    assert got["imgs"] == 0
    assert got["inlineScripts"] == 0
    assert not any("c" == c.strip() for c in got["comments"])


FONT_JS = """
const faces = [...document.fonts].filter((f) => f.family.replace(/["']/g, '') === 'Architects Daughter');
return {
  family: getComputedStyle(document.querySelector('.pad-name')).fontFamily,
  bodyFamily: getComputedStyle(document.body).fontFamily,
  statuses: faces.map((f) => f.status),
  checks: document.fonts.check('16px "Architects Daughter"'),
};
"""


def _delete_font(round_dir):
    (round_dir / "ArchitectsDaughter-Regular.ttf").unlink()


def test_lettering_uses_architects_daughter_loaded_from_the_round_folder(tmp_path):
    got = _run(tmp_path, _round(1), FONT_JS)
    assert "Architects Daughter" in got["family"]
    assert "Architects Daughter" in got["bodyFamily"]
    assert got["statuses"] == ["loaded"]


def test_lettering_font_is_not_loaded_when_its_file_is_missing(tmp_path):
    got = _run(tmp_path, _round(1), FONT_JS, mutate=_delete_font)
    assert "Architects Daughter" in got["family"]
    assert got["statuses"] == ["error"]


def test_top_level_index_lands_on_round_one_from_disk(tmp_path):
    pad = _build(tmp_path, _round(1))
    got = ch.run_probe(
        pad,
        SNAPSHOT + "return {href: location.href, cards: cards.length};",
        entry="index.html",
        inject_into="round-1/index.html",
    )
    assert got["href"].startswith("file://")
    assert got["href"].endswith("/round-1/index.html")
    assert got["cards"] == 2


def _delete_round(round_dir):
    (round_dir / "round.js").unlink()


def _break_round(round_dir):
    (round_dir / "round.js").write_text("window.PAD_ROUND = {;\n")


STATE_JS = """
return {
  failure: text(failure),
  loading: !!loading,
  cards: document.querySelectorAll('.pad-card').length,
  ready: document.documentElement.hasAttribute('data-pad-ready'),
};
"""


def _no_round(round_dir):
    (round_dir / "round.js").write_text("window.PAD_ROUND = {};\n")


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (_delete_round, "did not load"),
        (_break_round, "did not load"),
        (_no_round, "does not define a round"),
    ],
    ids=["missing", "syntax-error", "defines-no-round"],
)
def test_unreadable_round_shows_could_not_be_drawn_with_a_reason(tmp_path, mutate, reason):
    got = _run(tmp_path, _round(1), STATE_JS, mutate=mutate)
    assert got["failure"].startswith("This pad could not be drawn: ")
    assert reason in got["failure"]
    assert got["cards"] == 0
    assert not got["loading"]
    assert got["ready"]


def test_page_script_throwing_shows_could_not_be_drawn_with_the_error(tmp_path):
    def _malformed(round_dir):
        malformed = {"title": "T", "questions": [{"prompt": "Q", "options": "not a list"}]}
        (round_dir / "round.js").write_text("window.PAD_ROUND = " + json.dumps(malformed) + ";\n")

    got = _run(tmp_path, _round(1), STATE_JS, mutate=_malformed)
    assert got["failure"].startswith("This pad could not be drawn: ")
    assert got["cards"] == 0
    assert not got["loading"]
    assert got["ready"]


def test_valid_round_leaves_no_loading_or_failure_line(tmp_path):
    got = _run(tmp_path, _round(1), STATE_JS)
    assert got["failure"] is None
    assert not got["loading"]
    assert got["cards"] == 2


NOTICE_JS = """
const notice = document.getElementById('pad-font-notice');
return {visible: !!notice && shown(notice), text: notice ? notice.textContent : null};
"""


def test_font_notice_is_visible_when_the_font_did_not_load(tmp_path):
    got = _run(tmp_path, _round(1), NOTICE_JS, mutate=_delete_font)
    assert got["visible"]
    assert "font" in got["text"].lower()


def test_font_notice_is_hidden_when_the_font_loaded(tmp_path):
    got = _run(tmp_path, _round(1), NOTICE_JS)
    assert not got["visible"]


def test_question_entries_are_buttons_and_ink_layer_is_hidden_from_assistive_tech(tmp_path):
    js = """
    return {
      entryTags: [...document.querySelectorAll('nav li > *')].map((e) => e.tagName),
      current: document.querySelectorAll('[aria-current]').length,
      inks: [...document.querySelectorAll('.pad-card .pad-ink')].map((s) => ({
        tag: s.tagName.toLowerCase(),
        hidden: s.getAttribute('aria-hidden'),
        inDrawing: s.parentElement.classList.contains('pad-drawing'),
      })),
      drawings: [...document.querySelectorAll('.pad-drawing')].map((d) => [d.dataset.question, d.dataset.option]),
    };
    """
    got = _run(tmp_path, _round(3), js)
    assert got["entryTags"] == ["BUTTON"] * 4
    assert got["current"] == 1
    assert got["inks"] == [{"tag": "svg", "hidden": "true", "inDrawing": True}] * 2
    assert got["drawings"] == [["0", "0"], ["0", "1"]]


MESSAGE_JS = """
return {
  messages: [...document.querySelectorAll('.pad-drawing .pen-message')].map((m) => m.textContent),
  drawings: document.querySelectorAll('.pad-drawing').length,
  names: cards.map((c) => c.name),
  picks: cards.map((c) => c.pick),
  failure: text(failure),
  ready: document.documentElement.hasAttribute('data-pad-ready'),
};
"""


def _delete_pen(round_dir):
    (round_dir / "pen.js").unlink()


def test_missing_pen_shows_the_cant_draw_message_in_every_drawing_and_keeps_the_cards(tmp_path):
    got = _run(tmp_path, _round(1), MESSAGE_JS, mutate=_delete_pen)
    assert got["drawings"] == 2
    assert len(got["messages"]) == 2
    assert all(m.startswith("Can't draw this layout") and "sketch pen did not load" in m for m in got["messages"])
    assert got["names"] == ["Q0 opt 0", "Q0 opt 1"]
    assert got["picks"] == [True, False]
    assert got["failure"] is None
    assert got["ready"]


def test_loaded_pen_shows_no_cant_draw_message_for_a_readable_layout(tmp_path):
    got = _run(tmp_path, _round(1), MESSAGE_JS)
    assert got["messages"] == []
    assert got["names"] == ["Q0 opt 0", "Q0 opt 1"]
    assert got["ready"]


# --- rounds -------------------------------------------------------------------------------------------

ROUNDS = """
const switcher = document.querySelector('nav.pad-rounds');
const links = switcher ? [...switcher.querySelectorAll('a')].map((a) => ({
  text: a.querySelector('.pad-round-text').textContent,
  href: a.getAttribute('href'),
  title: a.getAttribute('title'),
  target: a.getAttribute('target'),
  rel: a.getAttribute('rel'),
  hidden: a.querySelector('.pad-sr') ? a.querySelector('.pad-sr').textContent : null,
  current: a.getAttribute('aria-current'),
  children: a.querySelectorAll('img, script').length,
})) : null;
const currentEl = switcher ? switcher.querySelector('[aria-current="page"]') : null;
const currentText = currentEl ? currentEl.querySelector('.pad-round-text').textContent : null;
const current = currentEl ? { tag: currentEl.tagName, text: currentText } : null;
const settled = document.querySelector('details.pad-settled');
const settledState = settled ? {
  open: settled.open,
  summary: settled.querySelector('summary').textContent,
  items: [...settled.querySelectorAll('li')].map((li) => li.textContent),
  injected: settled.querySelectorAll('img, script, [onerror]').length,
} : null;
const built = window.PadReply.build(window.PAD_ROUND, window.PadAnswers.answers);
const reply = JSON.parse(built.replace(/^```json\\n|\\n```$/g, ''));
"""


def _env(tmp_path):
    return {
        "OUTPOST_STATE_DIR": str(tmp_path / "state"),
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
        "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
    }


def _build_rounds(tmp_path, rounds):
    """Build a pad with the real script: the first round, then each later one with --add."""
    env = _env(tmp_path)
    cwd = tmp_path / "cwd"
    pad = None
    for i, data in enumerate(rounds):
        round_path = tmp_path / f"round-{i}.json"
        round_path.write_text(json.dumps(data))
        if i == 0:
            _, pad = scratch_pad.build_pad(round_path, "demo", env=env, cwd=cwd)
        else:
            assert scratch_pad.main(["--pad", "demo", "--round", str(round_path), "--add"], env=env, cwd=cwd) == 0
    return pad


def _view(pad, k, js, **kw):
    entry = f"round-{k}/index.html"
    return ch.run_probe(pad, SNAPSHOT + ROUNDS + js, entry=entry, inject_into=entry, **kw)


LONG_TITLE = "A very long title that keeps going well past what a switcher entry has room for " * 2


def test_a_one_round_pad_shows_no_switcher_and_its_reply_carries_round_1(tmp_path):
    pad = _build_rounds(tmp_path, [_round(1)])
    js = "return {links, reply, firstChild: document.querySelector('.pad-head').firstElementChild.tagName};"
    got = _view(pad, 1, js)
    assert got["links"] is None
    assert got["firstChild"] == "H1"
    assert list(got["reply"])[:2] == ["pad", "round"]
    assert got["reply"]["round"] == 1


def test_a_pad_without_a_round_list_draws_without_a_switcher_and_its_reply_has_no_round(tmp_path):
    pad = _build_rounds(tmp_path, [_round(1)])
    (pad / "rounds.js").unlink()
    round_js = pad / "round-1" / "round.js"
    kept = [line for line in round_js.read_text().splitlines(True) if "PAD_ROUND_NUMBER" not in line]
    round_js.write_text("".join(kept))
    got = _view(pad, 1, "return {links, reply, entries, loading: !!loading, failure: !!failure};")
    assert got["links"] is None
    assert got["entries"] == [{"text": "Q0", "current": "true"}]
    assert not got["loading"] and not got["failure"]
    assert "round" not in got["reply"]


def test_an_unreadable_round_list_draws_without_a_switcher(tmp_path):
    pad = _build_rounds(tmp_path, [_round(1), _round(1, title="Second")])
    (pad / "rounds.js").write_text("window.PAD_ROUNDS = 'not a list';\n")
    got = _view(pad, 2, "return {links, entries, failure: !!failure};")
    assert got["links"] is None
    assert got["entries"] == [{"text": "Q0", "current": "true"}]
    assert not got["failure"]


def test_the_latest_of_three_rounds_links_every_earlier_round_by_its_own_folder(tmp_path):
    rounds = [_round(1, title=LONG_TITLE), _round(1, title="Second"), _round(1, title="Third")]
    pad = _build_rounds(tmp_path, rounds)
    got = _view(pad, 3, "return {links, current, reply, note: document.querySelector('.pad-rounds-note')};")
    assert [link["href"] for link in got["links"]] == ["../round-1/index.html", "../round-2/index.html"]
    assert [link["text"].split(":")[0] for link in got["links"]] == ["Round 1", "Round 2"]
    long_link = got["links"][0]
    assert long_link["text"].endswith("…")
    assert len(long_link["text"]) < len(LONG_TITLE)
    assert long_link["title"] == LONG_TITLE
    assert got["links"][1]["text"] == "Round 2: Second"
    assert got["links"][1]["title"] == "Second"
    for link in got["links"]:
        assert (link["target"], link["rel"], link["hidden"]) == ("_blank", "noopener", " (opens in a new tab)")
        assert link["current"] is None
    assert got["current"] == {"tag": "SPAN", "text": "Round 3: Third"}
    assert got["note"] is None
    assert got["reply"]["round"] == 3


def test_an_earlier_round_says_which_round_it_is_and_links_to_the_latest(tmp_path):
    rounds = [_round(1, title="First"), _round(1, title="Second"), _round(1, title="Third")]
    pad = _build_rounds(tmp_path, rounds)
    js = """
    const note = document.querySelector('.pad-rounds-note');
    const latestLink = note.querySelector('a');
    return {links, current, reply, note: note.firstChild.textContent,
            latest: latestLink.getAttribute('href'), latestTarget: latestLink.getAttribute('target')};
    """
    got = _view(pad, 1, js)
    assert got["current"]["text"] == "Round 1: First"
    assert [link["href"] for link in got["links"]] == ["../round-2/index.html", "../round-3/index.html"]
    assert got["note"] == "This is round 1 of 3. "
    assert got["latest"] == "../round-3/index.html"
    assert got["latestTarget"] == "_blank"
    assert got["reply"]["round"] == 1


def test_a_settled_list_is_folded_counts_its_lines_and_shows_none_when_absent_or_empty(tmp_path):
    pad = _build_rounds(tmp_path, [_round(1, settled=["Header: B", "Nav on the left"])])
    got = _view(pad, 1, "return {settledState, text: document.querySelector('.pad-head').textContent};")
    assert got["settledState"]["open"] is False
    assert got["settledState"]["summary"] == "Settled in earlier rounds (2)"
    assert got["settledState"]["items"] == ["Header: B", "Nav on the left"]

    for tag, extra in (("absent", {}), ("empty", {"settled": []})):
        sub = tmp_path / tag
        sub.mkdir()
        (sub / "cwd").mkdir()
        pad = _build_rounds(sub, [_round(1, **extra)])
        got = _view(pad, 1, "return {settledState, text: document.querySelector('.pad-head').textContent};")
        assert got["settledState"] is None, tag
        assert "Settled" not in got["text"], tag


def test_hostile_settled_lines_and_round_titles_show_as_literal_text(tmp_path):
    script, img = HOSTILE[0], HOSTILE[1]
    rounds = [_round(1, title=img), _round(1, title=script), _round(1, title="Third", settled=[script, img])]
    pad = _build_rounds(tmp_path, rounds)
    got = _view(pad, 3, "return {links, settledState, pwned: window.pwned === undefined ? null : window.pwned};")
    assert [link["text"] for link in got["links"]] == [f"Round 1: {img}", f"Round 2: {script}"]
    assert [link["children"] for link in got["links"]] == [0, 0]
    assert got["settledState"]["items"] == [script, img]
    assert got["settledState"]["injected"] == 0
    assert got["pwned"] is None

    got = _view(pad, 2, "return {current, links, pwned: window.pwned === undefined ? null : window.pwned};")
    assert got["current"]["text"] == f"Round 2: {script}"
    assert [link["text"] for link in got["links"]] == [f"Round 1: {img}", "Round 3: Third"]
    assert got["pwned"] is None


def _with_flow_option(data):
    data["questions"][0]["options"][1] = {"name": "F", "cost": "c", "flow": "flowchart LR\n subgraph x"}
    return data


def test_missing_pen_names_a_flow_option_a_flow_and_a_layout_option_a_layout(tmp_path):
    got = _run(tmp_path, _with_flow_option(_round(1)), MESSAGE_JS, mutate=_delete_pen)
    assert got["messages"] == [
        "Can't draw this layout:The sketch pen did not load.",
        "Can't draw this flow:The sketch pen did not load.",
    ]


def test_a_flow_option_shows_the_pens_cant_draw_message_and_its_layout_sibling_draws(tmp_path):
    got = _run(tmp_path, _with_flow_option(_round(1)), MESSAGE_JS)
    assert len(got["messages"]) == 1
    assert got["messages"][0].startswith("Can't draw this flow:")
    assert "line 2: subgraph x" in got["messages"][0]
    assert got["names"] == ["Q0 opt 0", "F"]
    assert got["ready"]
