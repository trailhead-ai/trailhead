"""Behavioural tests for the reference page: a page of only the chosen drawings, drawn by the pad's pen.

Every test builds a reference folder under ``tmp_path`` and opens it from ``file://`` in a headless browser
through the probe harness, then asserts on what the page rendered and the strokes the pen traced.
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
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)


PEN_LIB = """
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const strokesOf = (root) => [...root.querySelectorAll('svg.pad-ink path')].map((p) => p.getAttribute('d'));
"""

LAYOUT_A = {"column": [{"box": [{"text": "Alpha"}, {"button": "Go"}]}, {"rule": True}]}
LAYOUT_B = {"row": [{"box": [{"text": "Beta"}]}, {"box": [{"field": "Name"}]}, {"pill": "x"}]}
LAYOUT_C = {"box": [{"text": "Gamma"}, {"image": "pic"}]}


def _pad_round(*layouts):
    return {
        "title": "Pen",
        "questions": [
            {
                "prompt": "Q",
                "options": [
                    {"name": f"opt {i}", "cost": "c", "pick": i == 0, "layout": layout}
                    for i, layout in enumerate(layouts)
                ],
            }
        ],
    }


def test_the_pen_draws_an_option_handed_in_at_its_original_index_of_an_otherwise_empty_options_list(tmp_path):
    round_path = tmp_path / "round.json"
    round_path.write_text(json.dumps(_pad_round(LAYOUT_A, LAYOUT_B, LAYOUT_C)))
    env = {"OUTPOST_STATE_DIR": str(tmp_path / "state")}
    _, pad = scratch_pad.build_pad(round_path, "demo", env=env)
    got = ch.run_probe(
        pad / "round-1",
        PEN_LIB
        + """
const original = document.querySelector('.pad-drawing[data-option="2"]');
const full = strokesOf(original);
const option = window.PAD_ROUND.questions[0].options[2];
const panel = document.createElement('section');
panel.className = 'pad-question';
const d = document.createElement('div');
d.className = 'pad-drawing';
d.setAttribute('data-option', '2');
d.style.position = 'relative';
d.style.width = original.getBoundingClientRect().width + 'px';
const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
svg.setAttribute('class', 'pad-ink');
d.appendChild(svg);
panel.appendChild(d);
document.body.appendChild(panel);
const options = [];
options[2] = {layout: option.layout};
await window.PadPen.drawQuestion(panel, {options}, 0);
await sleep(100);
return {full, alone: strokesOf(d), count: full.length};
""",
    )
    assert got["count"] > 0
    assert got["alone"] == got["full"]


# --- the reference page ----------------------------------------------------------------------------------

PAGE_LIB = """
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const cards = () => [...document.querySelectorAll('.ref-card')];
const txt = (card, sel) => { const n = card.querySelector(sel); return n ? n.textContent : null; };
const strokesOf = (card) => [...card.querySelectorAll('svg.pad-ink path')].map((p) => p.getAttribute('d'));
const stepSel = 'svg.pad-ink g[data-kind]:not([data-kind="arrow"]):not([data-kind="loop"])';
const steps = (card) => card.querySelectorAll(stepSel).length;
const arrows = (card) => card.querySelectorAll('svg.pad-ink g[data-kind="arrow"]').length;
const marks = (card) => card.querySelectorAll('svg.pad-ink g[data-pen-index]').length;
const message = (card) => {
  const m = card.querySelector('.pen-message');
  return m ? [...m.querySelectorAll('p')].map((p) => p.textContent) : null;
};
const summary = (card) => ({
  prompt: txt(card, '.pad-prompt'),
  round: txt(card, '.ref-round'),
  letter: txt(card, '.pad-letter'),
  name: txt(card, '.pad-name'),
  cost: txt(card, '.pad-cost'),
});
"""

DATE = "2026-10-22"
FLOW_TWO = {"name": "F", "cost": "c", "pick": False, "flow": "flowchart LR\n  a[One] --> b[Two]"}


def _opt(name="Plain", cost="Cheap", pick=False, **shape):
    return {"name": name, "cost": cost, "pick": pick, **(shape or {"layout": LAYOUT_A})}


def _drawing(round=1, question=1, letter="A", round_title="Round one", prompt="Where does it go?", option=None):
    return scratch_pad.ChosenDrawing(round, question, letter, round_title, prompt, option or _opt())


def _reference(*drawings, title="What we are building"):
    return scratch_pad.Reference("the-site", title, list(drawings))


def _build(tmp_path, reference, date=DATE, name="out"):
    folder = tmp_path / name
    scratch_pad.build_reference_page(folder, reference, date)
    return folder


def _run(tmp_path, reference, js, date=DATE, **kwargs):
    folder = _build(tmp_path, reference, date)
    return ch.run_probe(folder, PAGE_LIB + js, **kwargs)


def test_a_reference_of_one_chosen_layout_shows_one_card_with_the_title_and_a_traced_drawing(tmp_path):
    ref = _reference(_drawing(option=_opt("Logo left", "Cheap")), title="Header decision")
    got = _run(
        tmp_path,
        ref,
        "return {title: document.querySelector('h1').textContent, doc: document.title, n: cards().length,"
        " card: summary(cards()[0]), marks: marks(cards()[0]), strokes: strokesOf(cards()[0]).length};",
    )
    assert got["title"] == "Header decision"
    assert got["doc"] == "Header decision"
    assert got["n"] == 1
    assert got["card"] == {
        "prompt": "Where does it go?",
        "round": "Round one",
        "letter": "A",
        "name": "Logo left",
        "cost": "Cheap",
    }
    assert got["marks"] > 0
    assert got["strokes"] > 0


def test_three_drawings_across_two_rounds_show_three_cards_in_the_order_named_each_with_its_own_text(tmp_path):
    ref = _reference(
        _drawing(2, 1, "B", "Sign-in round", "Wrong password?", _opt("Lock out", "Hard", layout=LAYOUT_B)),
        _drawing(1, 2, "A", "Header round", "Controls?", _opt("Inline", "Easy", layout=LAYOUT_C)),
        _drawing(1, 1, "C", "Header round", "Logo?", _opt("Centre", "Free", layout=LAYOUT_A)),
    )
    got = _run(tmp_path, ref, "return cards().map(summary);")
    assert got == [
        {"prompt": "Wrong password?", "round": "Sign-in round", "letter": "B", "name": "Lock out", "cost": "Hard"},
        {"prompt": "Controls?", "round": "Header round", "letter": "A", "name": "Inline", "cost": "Easy"},
        {"prompt": "Logo?", "round": "Header round", "letter": "C", "name": "Centre", "cost": "Free"},
    ]


def test_a_reference_read_from_a_pad_leaves_the_pads_other_options_out_of_every_file(tmp_path):
    pad_round = {
        "title": "Header round",
        "questions": [
            {
                "prompt": "Where does the logo go?",
                "options": [
                    _opt("Chosen one", "ChosenCost", True, layout={"box": [{"text": "ChosenLabel"}]}),
                    _opt("Other option", "OtherCost", layout={"box": [{"text": "OtherLabel"}]}),
                ],
            }
        ],
    }
    env = {
        "OUTPOST_STATE_DIR": str(tmp_path / "state"),
        "CAMP_STATE_DIR": str(tmp_path / "camp-state"),
        "LORE_STATE_DIR": str(tmp_path / "lore-state"),
        "XDG_STATE_HOME": str(tmp_path / "xdg"),
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/bin:/bin",
    }
    round_path = tmp_path / "round.json"
    round_path.write_text(json.dumps(pad_round))
    scratch_pad.build_pad(round_path, "pad", env=env, cwd=tmp_path / "cwd")
    ref_path = tmp_path / "ref.json"
    ref_path.write_text(
        json.dumps({"site": "s", "title": "T", "drawings": [{"round": 1, "question": 1, "option": "A"}]})
    )
    reference = scratch_pad.read_reference(ref_path, "pad", env=env, cwd=tmp_path / "cwd")
    folder = _build(tmp_path, reference)
    corpus = "".join(
        p.read_text(encoding="utf-8", errors="ignore") for p in folder.rglob("*") if p.is_file() and p.suffix != ".ttf"
    )
    assert "ChosenLabel" in corpus
    for left_out in ("Other option", "OtherCost", "OtherLabel"):
        assert left_out not in corpus
    got = ch.run_probe(folder, PAGE_LIB + "return cards().map(summary);")
    assert [c["name"] for c in got] == ["Chosen one"]


def test_an_option_key_the_pad_does_not_check_never_reaches_the_pages_files(tmp_path):
    option = _opt("Plain", "Cheap")
    option["secret"] = "TOPSECRETVALUE"
    option["extra"] = {"layout": {"text": "SMUGGLED"}}
    folder = _build(tmp_path, _reference(_drawing(option=option)))
    corpus = "".join(
        p.read_text(encoding="utf-8", errors="ignore") for p in folder.rglob("*") if p.is_file() and p.suffix != ".ttf"
    )
    assert "Plain" in corpus
    assert "TOPSECRETVALUE" not in corpus
    assert "SMUGGLED" not in corpus


def test_a_chosen_layout_traces_its_boxes(tmp_path):
    got = _run(
        tmp_path,
        _reference(_drawing(option=_opt("L", layout=LAYOUT_B))),
        "const c = cards()[0]; return {marks: marks(c), steps: steps(c), arrows: arrows(c), msg: message(c)};",
    )
    assert got["marks"] >= 3
    assert got["steps"] == got["marks"]
    assert got["arrows"] == 0
    assert got["msg"] is None


def test_a_chosen_flow_draws_its_steps_and_arrows(tmp_path):
    flow = {"name": "F", "cost": "c", "pick": False, "flow": "flowchart LR\n  a[One] --> b[Two] --> c[Three]"}
    got = _run(
        tmp_path,
        _reference(_drawing(option=flow)),
        "const c = cards()[0]; return {steps: steps(c), arrows: arrows(c), msg: message(c)};",
    )
    assert got == {"steps": 3, "arrows": 2, "msg": None}


def test_a_chosen_option_the_pen_cannot_draw_shows_the_message_in_its_card_while_the_others_draw(tmp_path):
    broken_layout = _opt("Broken layout", layout={"box": [{"nonsense": "x"}]})
    broken_flow = {"name": "Broken flow", "cost": "c", "pick": False, "flow": "flowchart LR\n  a[One] --> --> b"}
    ref = _reference(
        _drawing(letter="A", option=_opt("Fine", layout=LAYOUT_A)),
        _drawing(letter="B", option=broken_layout),
        _drawing(letter="C", option=broken_flow),
        _drawing(letter="D", option=_opt("Also fine", layout=LAYOUT_C)),
    )
    got = _run(tmp_path, ref, "return cards().map((c) => ({msg: message(c), marks: marks(c)}));")
    assert got[0]["msg"] is None and got[0]["marks"] > 0
    assert got[3]["msg"] is None and got[3]["marks"] > 0
    assert got[1]["msg"][0] == "Can't draw this layout:"
    assert '"nonsense"' in "".join(got[1]["msg"])
    assert got[2]["msg"][0] == "Can't draw this flow:"
    assert got[2]["msg"][1].startswith("line 2")


def test_the_same_page_loaded_twice_at_one_width_draws_the_same_strokes(tmp_path):
    ref = _reference(
        _drawing(letter="A", option=_opt("L", layout=LAYOUT_B)),
        _drawing(letter="B", option=FLOW_TWO),
    )
    folder = _build(tmp_path, ref)
    js = PAGE_LIB + "return cards().map(strokesOf);"
    first = ch.run_probe(folder, js)
    second = ch.run_probe(folder, js)
    assert first == second
    assert all(len(strokes) > 0 for strokes in first)


def test_two_different_chosen_options_draw_different_strokes(tmp_path):
    ref = _reference(
        _drawing(letter="A", option=_opt("L", layout=LAYOUT_A)),
        _drawing(letter="A", option=_opt("M", layout=LAYOUT_B)),
    )
    got = _run(tmp_path, ref, "return cards().map(strokesOf);")
    assert got[0] and got[1]
    assert got[0] != got[1]


def test_an_option_is_drawn_from_its_original_place_so_the_same_option_elsewhere_draws_other_strokes(tmp_path):
    ref = _reference(
        _drawing(question=1, letter="A", option=_opt("L", layout=LAYOUT_A)),
        _drawing(question=2, letter="A", option=_opt("L", layout=LAYOUT_A)),
        _drawing(question=1, letter="B", option=_opt("L", layout=LAYOUT_A)),
    )
    got = _run(tmp_path, ref, "return cards().map(strokesOf);")
    assert got[0] != got[1]
    assert got[0] != got[2]
    assert got[1] != got[2]


HOSTILE = ["<script>window.pwned=1</script>", "<img src=x onerror=window.pwned=1>"]


def _hostile_reference(field, text):
    option = _opt("Plain", "Cheap", layout=LAYOUT_A)
    kw = {}
    title = "Plain title"
    if field == "title":
        title = text
    elif field == "prompt":
        kw["prompt"] = text
    elif field == "round_title":
        kw["round_title"] = text
    elif field == "name":
        option["name"] = text
    elif field == "cost":
        option["cost"] = text
    elif field == "layout label":
        option["layout"] = {"box": [{"text": text}]}
    elif field == "flow label":
        option = {"name": "F", "cost": "c", "pick": False, "flow": f'flowchart LR\n  a["{text}"] --> b[Two]'}
    return _reference(_drawing(option=option, **kw), title=title)


@pytest.mark.parametrize("text", HOSTILE)
@pytest.mark.parametrize(
    "field", ["title", "prompt", "round_title", "name", "cost", "layout label", "flow label"]
)
def test_hostile_text_in_a_field_shows_as_those_characters_and_nothing_runs(tmp_path, field, text):
    got = _run(
        tmp_path,
        _hostile_reference(field, text),
        """
await sleep(200);
const body = document.body.innerText + [...document.querySelectorAll('svg text')].map((t) => t.textContent).join('|');
return {pwned: window.pwned === undefined ? null : window.pwned,
        shown: body,
        images: document.querySelectorAll('img').length,
        inline: [...document.querySelectorAll('script')].filter((s) => !s.src).length};
""",
    )
    assert got["pwned"] is None
    assert got["images"] == 0
    assert got["inline"] == 0
    assert text in got["shown"]


def test_the_date_line_shows_the_date_the_builder_was_given_and_another_date_gives_another_line(tmp_path):
    ref = _reference(_drawing())
    js = "return document.querySelector('#ref-chosen').textContent;"
    first = ch.run_probe(_build(tmp_path, ref, "2026-10-22", "a"), PAGE_LIB + js)
    second = ch.run_probe(_build(tmp_path, ref, "2027-01-02", "b"), PAGE_LIB + js)
    assert "2026-10-22" in first and "2027-01-02" not in first
    assert "2027-01-02" in second and "2026-10-22" not in second
    assert "scratch pad" in first
    assert "sketches" in first


def test_the_builder_dates_the_page_today_when_given_no_date(tmp_path):
    import datetime

    folder = tmp_path / "out"
    scratch_pad.build_reference_page(folder, _reference(_drawing()))
    got = ch.run_probe(folder, PAGE_LIB + "return document.querySelector('#ref-chosen').textContent;")
    assert datetime.date.today().isoformat() in got


def test_the_font_notice_shows_when_the_font_file_is_gone_and_stays_hidden_when_it_is_there(tmp_path):
    ref = _reference(_drawing())
    js = "return document.getElementById('pad-font-notice').hidden;"
    present = _build(tmp_path, ref, name="present")
    absent = _build(tmp_path, ref, name="absent")
    (absent / "ArchitectsDaughter-Regular.ttf").unlink()
    assert ch.run_probe(present, PAGE_LIB + js) is True
    assert ch.run_probe(absent, PAGE_LIB + js) is False


CONTROLS = {
    "pick": ".pad-pick, [aria-label^='This one'], .pad-toggle",
    "reject": "[aria-label^='Not this'], .pad-choices",
    "none": ".pad-none",
    "note": "textarea, .pad-note, .pad-note-label",
    "copy": ".pad-copy, .pad-reply-box, #pad-reply",
    "round switcher": ".pad-rounds, .pad-rounds-note, a, nav, select",
    "saved answers": "#pad-cant-keep, .pad-mark, .pad-dot, .pad-questions",
}
CONTROL_WORDS = {
    "pick": ["This one", "my pick"],
    "reject": ["Not this"],
    "none": ["None of these"],
    "note": ["Note"],
    "copy": ["Copy"],
    "round switcher": ["Rounds", "round "],
    "saved answers": ["can't keep your answers"],
}


@pytest.mark.parametrize("control", list(CONTROLS))
def test_the_page_has_no_such_control_though_its_source_pad_option_is_picked(tmp_path, control):
    option = _opt("Picked", "Cheap", pick=True, layout=LAYOUT_A)
    got = _run(
        tmp_path,
        _reference(_drawing(option=option), _drawing(letter="B", option=_opt("Second", layout=LAYOUT_B))),
        f"""
await sleep(100);
const labels = [...document.querySelectorAll('[aria-label]')].map((n) => n.getAttribute('aria-label'));
return {{found: document.querySelectorAll({json.dumps(CONTROLS[control])}).length,
        text: document.body.innerText + labels.join(' ')}};
""",
    )
    assert got["found"] == 0
    for word in CONTROL_WORDS[control]:
        assert word not in got["text"]


def test_the_page_loads_only_files_beside_it_and_runs_under_the_pads_policy(tmp_path):
    folder = _build(tmp_path, _reference(_drawing()))
    html_text = (folder / "index.html").read_text(encoding="utf-8")
    policy = "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'"
    assert policy in html_text
    got = ch.run_probe(
        folder,
        PAGE_LIB
        + "return {srcs: [...document.querySelectorAll('script[src]')].map((s) => s.getAttribute('src')),"
        " styles: [...document.querySelectorAll('link[rel=stylesheet]')].map((s) => s.getAttribute('href'))};",
    )
    for ref in [r for r in got["srcs"] + got["styles"] if r != ch.PROBE_FILE]:
        assert "/" not in ref
        assert (folder / ref).is_file()


def test_the_data_file_is_ascii_json_handed_to_json_parse_and_non_ascii_text_still_shows(tmp_path):
    name = "Café ☕ </script>"
    folder = _build(tmp_path, _reference(_drawing(option=_opt(name, "Cheap"))))
    data_js = (folder / "reference-data.js").read_text(encoding="utf-8")
    assert data_js.startswith("window.PAD_REFERENCE = JSON.parse(")
    assert data_js.isascii()
    got = ch.run_probe(folder, PAGE_LIB + "return summary(cards()[0]).name;")
    assert got == name
