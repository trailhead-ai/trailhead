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
