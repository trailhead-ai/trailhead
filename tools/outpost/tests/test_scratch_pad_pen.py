"""Behavioural tests for the scratch-pad kit's pen.

Every test builds a real pad with ``scratch_pad.build_pad`` from the real kit, opens the built
round folder from ``file://`` in a headless browser through the probe harness, and asserts on
the strokes the pen traced and the elements the browser laid out.
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
]

LIB = """
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const drawing = (q, o) =>
  document.querySelector(`.pad-drawing[data-question="${q}"][data-option="${o}"]`);
const inkOf = (d) => d.querySelector('svg.pad-ink');
const rel = (el, d) => {
  const b = inkOf(d).getBoundingClientRect();
  const r = el.getBoundingClientRect();
  return {x: r.left - b.left, y: r.top - b.top, w: r.width, h: r.height};
};
const points = (d) => {
  const n = (d.match(/-?\\d+(\\.\\d+)?/g) || []).map(Number);
  const out = [];
  for (let i = 0; i + 1 < n.length; i += 2) out.push([n[i], n[i + 1]]);
  return out;
};
const targetOf = (d, index) => {
  const el = d.querySelector(`[data-pen-index="${index}"]`);
  return el.querySelector('.pen-input') || el;
};
const marksOf = (d) =>
  [...inkOf(d).querySelectorAll('g')].map((g) => {
    const bb = g.getBBox();
    const index = Number(g.getAttribute('data-pen-index'));
    return {
      index,
      kind: g.getAttribute('data-kind'),
      paths: [...g.querySelectorAll('path')].map((p) => points(p.getAttribute('d'))),
      box: {x: bb.x, y: bb.y, w: bb.width, h: bb.height},
      element: rel(targetOf(d, index), d),
    };
  });
const gap = (a, b) =>
  Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y), Math.abs(a.w - b.w), Math.abs(a.h - b.h));
const corner = (mark) => {
  const e = mark.element;
  return Math.min(
    ...mark.paths.map((p) => Math.min(...p.map(([x, y]) => Math.hypot(x - e.x, y - e.y))))
  );
};
const messageOf = (d) => {
  const m = d.querySelector('.pen-message');
  return m ? [...m.querySelectorAll('p')].map((p) => p.textContent) : null;
};
const settle = () => sleep(100);
const bareDrawing = () => {
  const panel = document.createElement('section');
  panel.className = 'pad-question';
  const d = document.createElement('div');
  d.className = 'pad-drawing';
  d.setAttribute('data-option', '0');
  d.style.position = 'relative';
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'pad-ink');
  d.appendChild(svg);
  panel.appendChild(d);
  document.body.appendChild(panel);
  return {panel, d};
};
const until = async (predicate, ms = 40000) => {
  for (let waited = 0; waited < ms && !predicate(); waited += 50) await sleep(50);
};
"""

TOL = 4.0


def gap(a, b):
    return max(abs(a[k] - b[k]) for k in ("x", "y", "w", "h"))


def _round(*questions):
    return {
        "title": "Pen",
        "questions": [
            {
                "prompt": f"Q{qi}",
                "options": [
                    {"name": f"opt {oi}", "cost": "cost", "pick": oi == 0, "layout": layout}
                    for oi, layout in enumerate(layouts)
                ],
            }
            for qi, layouts in enumerate(questions)
        ],
    }


def _run(tmp_path, data, js, **kwargs):
    round_path = tmp_path / "round.json"
    round_path.write_text(json.dumps(data))
    env = {"OUTPOST_STATE_DIR": str(tmp_path / "state")}
    _, pad = scratch_pad.build_pad(round_path, "demo", env=env)
    return ch.run_probe(pad / "round-1", LIB + js, **kwargs)


def box(*blocks):
    return {"box": list(blocks)}


def row(*blocks):
    return {"row": list(blocks)}


def column(*blocks):
    return {"column": list(blocks)}


def button(label):
    return {"button": label}


def test_every_boxed_block_has_a_mark_matching_its_element_box(tmp_path):
    layout = box(button("Save"), button("Cancel"))
    got = _run(tmp_path, _round([layout]), "await settle(); return marksOf(drawing(0, 0));")
    assert [m["kind"] for m in got] == ["box", "button", "button"]
    for mark in got:
        assert mark["element"]["w"] > 20 and mark["element"]["h"] > 10
        assert gap(mark["box"], mark["element"]) <= TOL, mark


def test_a_row_lays_blocks_side_by_side_and_a_box_stacks_them(tmp_path):
    buttons = [button("One"), button("Two"), button("Three")]
    data = _round([row(*buttons), box(*buttons)])
    js = """
    await settle();
    const pick = (o) => marksOf(drawing(0, o)).filter((m) => m.kind === 'button');
    return {row: pick(0), box: pick(1)};
    """
    got = _run(tmp_path, data, js)
    for mark in got["row"] + got["box"]:
        assert gap(mark["box"], mark["element"]) <= TOL
    xs = [m["box"]["x"] for m in got["row"]]
    ys = [m["box"]["y"] for m in got["row"]]
    assert xs == sorted(xs) and xs[2] - xs[0] > 60
    assert max(ys) - min(ys) <= TOL
    xs = [m["box"]["x"] for m in got["box"]]
    ys = [m["box"]["y"] for m in got["box"]]
    assert ys == sorted(ys) and ys[2] - ys[0] > 40
    assert max(xs) - min(xs) <= TOL


def test_text_row_and_column_leave_no_mark(tmp_path):
    layout = column(row({"text": "hello"}, {"text": "there"}), {"text": "again"})
    js = """
    await settle();
    const d = drawing(0, 0);
    return {
      marks: marksOf(d).length,
      lettering: [...d.querySelectorAll('.pen-label')].map((e) => e.textContent),
    };
    """
    got = _run(tmp_path, _round([layout]), js)
    assert got["marks"] == 0
    assert got["lettering"] == ["hello", "there", "again"]


def test_field_traces_its_input_box_below_its_label(tmp_path):
    layout = box({"field": "Email"})
    js = """
    await settle();
    const d = drawing(0, 0);
    const field = marksOf(d).find((m) => m.kind === 'field');
    const label = rel(d.querySelector('.pen-label'), d);
    return {field, label};
    """
    got = _run(tmp_path, _round([layout]), js)
    assert gap(got["field"]["box"], got["field"]["element"]) <= TOL
    assert got["field"]["element"]["y"] >= got["label"]["y"] + got["label"]["h"] - 1


def test_image_crosses_both_diagonals_where_a_button_does_not(tmp_path):
    data = _round([box({"image": "Hero"}), box(button("Hero"))])
    js = """
    await settle();
    const diagonals = (m) => {
      const e = m.element;
      const near = (p, x, y) => Math.hypot(p[0] - x, p[1] - y) <= 4;
      const down = m.paths.filter((p) =>
        near(p[0], e.x, e.y) && near(p[p.length - 1], e.x + e.w, e.y + e.h));
      const up = m.paths.filter((p) =>
        near(p[0], e.x + e.w, e.y) && near(p[p.length - 1], e.x, e.y + e.h));
      return {down: down.length, up: up.length, paths: m.paths.length};
    };
    const mark = (o, kind) => marksOf(drawing(0, o)).find((m) => m.kind === kind);
    return {image: diagonals(mark(0, 'image')), button: diagonals(mark(1, 'button'))};
    """
    got = _run(tmp_path, data, js)
    assert got["image"] == {"down": 2, "up": 2, "paths": 6}
    assert got["button"]["down"] == 0 and got["button"]["up"] == 0


def test_rule_is_one_line_across_its_container(tmp_path):
    data = _round([box({"text": "above"}, {"rule": True}), box({"text": "above"})])
    js = """
    await settle();
    const d = drawing(0, 0);
    const rule = marksOf(d).find((m) => m.kind === 'rule');
    const parent = d.querySelector(`[data-pen-index="${rule.index}"]`).parentElement;
    const cs = getComputedStyle(parent);
    const inner = {
      w: parent.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight),
    };
    const ys = rule.paths.flat().map((p) => p[1]);
    return {
      rule,
      innerWidth: inner.w,
      slope: Math.max(...ys) - Math.min(...ys),
      without: marksOf(drawing(0, 1)).filter((m) => m.kind === 'rule').length,
    };
    """
    got = _run(tmp_path, data, js)
    assert len(got["rule"]["paths"]) == 2
    assert abs(got["rule"]["box"]["w"] - got["innerWidth"]) <= TOL
    assert got["rule"]["box"]["w"] > 100
    assert got["slope"] <= 4
    assert got["without"] == 0


@pytest.mark.parametrize(("kind", "label", "least"), [("pill", "Go", 5.0), ("button", "Go", 3.3)])
def test_rounded_kinds_round_their_corners_where_a_box_does_not(tmp_path, kind, label, least):
    layout = box({kind: label})
    js = """
    await settle();
    const marks = marksOf(drawing(0, 0));
    return {outer: corner(marks[0]), inner: corner(marks[1])};
    """
    got = _run(tmp_path, _round([layout]), js)
    assert got["outer"] < 2.2
    assert got["inner"] > least


def test_each_stroke_is_drawn_twice_and_wobbles(tmp_path):
    layout = box(button("Save"), {"pill": "Tag"})
    js = """
    await settle();
    const edge = (m) => {
      const all = m.paths.flat();
      const xs = all.map((p) => p[0]);
      const ys = all.map((p) => p[1]);
      const lo = [Math.min(...xs), Math.min(...ys)];
      const hi = [Math.max(...xs), Math.max(...ys)];
      const onEdge = all.filter(
        (p) => p[0] === lo[0] || p[0] === hi[0] || p[1] === lo[1] || p[1] === hi[1]
      ).length;
      return onEdge / all.length;
    };
    return marksOf(drawing(0, 0)).map((m) => ({
      kind: m.kind,
      count: m.paths.length,
      same: JSON.stringify(m.paths[0]) === JSON.stringify(m.paths[1]),
      edgeShare: edge(m),
    }));
    """
    got = _run(tmp_path, _round([layout]), js)
    assert len(got) == 3
    for mark in got:
        assert mark["count"] == 2
        assert not mark["same"]
        assert mark["edgeShare"] < 0.3


def test_a_question_shown_by_a_click_is_traced_at_its_real_size(tmp_path):
    data = _round([box(button("A"))], [box(button("B"), {"pill": "C"})])
    js = """
    document.querySelectorAll('nav button')[1].click();
    await settle();
    return marksOf(drawing(1, 0));
    """
    got = _run(tmp_path, data, js)
    assert [m["kind"] for m in got] == ["box", "button", "pill"]
    for mark in got:
        assert mark["element"]["w"] > 20 and mark["element"]["h"] > 10
        assert gap(mark["box"], mark["element"]) <= TOL


BAD_BLOCKS = [
    ({"slider": "Volume"}, '{"slider": "Volume"}', '"slider" is not a block kind.'),
    (
        {"text": "a", "pill": "b"},
        '{"text": "a", "pill": "b"}',
        "A block needs exactly one key; this one has 2.",
    ),
    ({"button": 3}, '{"button": 3}', '"button" needs a string.'),
    ({"box": "nope"}, '{"box": "nope"}', '"box" needs a list of blocks.'),
    ({}, "{}", "A block needs exactly one key; this one has none."),
]

STATE = """
const d0 = drawing(0, 0);
const d1 = drawing(0, 1);
const state = {
  failure: !!document.getElementById('pad-failure'),
  ready: document.documentElement.hasAttribute('data-pad-ready'),
  cards: document.querySelectorAll('.pad-card').length,
};
"""


@pytest.mark.parametrize(("bad", "quoted", "reason"), BAD_BLOCKS, ids=["kind", "two-keys", "value-type", "list-type", "empty"])
def test_an_unreadable_block_replaces_only_its_options_drawing_with_a_message(tmp_path, bad, quoted, reason):
    data = _round([box({"text": "a"}, {"text": "b"}, bad), box(button("fine"))])
    js = STATE + """
    await settle();
    return {
      ...state,
      message: messageOf(d0),
      svgs: d0.querySelectorAll('svg').length,
      sibling: marksOf(d1).map((m) => m.kind),
    };
    """
    got = _run(tmp_path, data, js)
    assert got["message"] == ["Can't draw this layout:", f"box › 2 › {quoted}", reason]
    assert got["svgs"] == 0
    assert got["sibling"] == ["box", "button"]
    assert got["cards"] == 2 and not got["failure"] and got["ready"]


@pytest.mark.parametrize("key", ["constructor", "toString"])
def test_names_the_pen_does_not_own_are_unknown_kinds(tmp_path, key):
    data = _round([{key: "x"}, box(button("fine"))])
    js = STATE + """
    await settle();
    return {...state, message: messageOf(d0), sibling: marksOf(d1).length};
    """
    got = _run(tmp_path, data, js)
    assert got["message"] == ["Can't draw this layout:", f'{{"{key}": "x"}}', f'"{key}" is not a block kind.']
    assert got["sibling"] == 2
    assert not got["failure"] and got["ready"]


def test_a_proto_key_in_a_layout_is_an_unknown_kind(tmp_path):
    js = """
    await settle();
    const {panel, d} = bareDrawing();
    const question = JSON.parse('{"options": [{"layout": {"__proto__": "x"}}]}');
    await window.PadPen.drawQuestion(panel, question, 9);
    return messageOf(d);
    """
    got = _run(tmp_path, _round([box(button("fine"))]), js)
    assert got == ["Can't draw this layout:", '{"__proto__": "x"}', '"__proto__" is not a block kind.']


def test_an_unexpected_error_while_building_shows_the_message_in_that_drawing(tmp_path):
    js = """
    await settle();
    const {panel, d} = bareDrawing();
    const layout = {get box() { throw new Error('boom'); }};
    await window.PadPen.drawQuestion(panel, {options: [{layout}]}, 9);
    return {message: messageOf(d), svgs: d.querySelectorAll('svg').length};
    """
    got = _run(tmp_path, _round([box(button("fine"))]), js)
    assert got["message"][0] == "Can't draw this layout:"
    assert got["message"][2] == "boom"
    assert got["svgs"] == 0


def test_a_null_layout_shows_the_message_not_an_empty_box(tmp_path):
    data = _round([None, box(button("fine"))])
    js = STATE + """
    await settle();
    return {...state, message: messageOf(d0), svgs: d0.querySelectorAll('svg').length, sibling: marksOf(d1).length};
    """
    got = _run(tmp_path, data, js)
    assert got["message"][0] == "Can't draw this layout:"
    assert got["message"][1] == "null"
    assert got["message"][2] == "A block is an object with exactly one key."
    assert got["svgs"] == 0 and got["sibling"] == 2
    assert not got["failure"] and got["ready"]


def test_a_layout_nested_five_hundred_deep_shows_the_message(tmp_path):
    deep = {"text": "bottom"}
    for _ in range(500):
        deep = {"box": [deep]}
    data = _round([deep, box(button("fine"))])
    js = STATE + """
    await settle();
    const m = messageOf(d0);
    return {...state, message: m, svgs: d0.querySelectorAll('svg').length, sibling: marksOf(d1).length};
    """
    got = _run(tmp_path, data, js)
    assert got["message"][0] == "Can't draw this layout:"
    assert "nested too deeply" in got["message"][2]
    assert len(got["message"][1]) < 400
    assert got["svgs"] == 0 and got["sibling"] == 2
    assert not got["failure"] and got["ready"]


# The headless browser only runs a rendering frame (where ResizeObserver reports) now and
# then, so the probe polls for the re-trace instead of sleeping a fixed time.
RESIZE = """
const d = drawing(0, 0);
await settle();
const before = marksOf(d);
const wide = before[0].element.w;
d.style.width = '300px';
await until(() => Math.abs(marksOf(d)[0].box.w - marksOf(d)[0].element.w) < 6);
const narrow = marksOf(d);
d.style.width = '';
await until(() => Math.abs(marksOf(d)[0].element.w - wide) < 1 && Math.abs(marksOf(d)[0].box.w - wide) < 6);
const restored = marksOf(d);
return {before, narrow, restored};
"""


def test_a_width_change_retraces_the_boxes_and_keeps_the_strokes_the_same_shape(tmp_path):
    data = _round([box(button("Save"), {"pill": "Tag"}, {"rule": True})])
    got = _run(tmp_path, data, RESIZE, virtual_time_budget=120000, timeout=120.0)
    assert got["narrow"][0]["element"]["w"] < got["before"][0]["element"]["w"] - 50
    for mark in got["narrow"]:
        want = mark["element"] if mark["kind"] != "rule" else {**mark["box"], "x": mark["element"]["x"], "w": mark["element"]["w"]}
        assert gap(mark["box"], want) <= TOL, mark
    assert got["restored"] == got["before"]


def test_each_option_and_question_has_its_own_strokes_but_a_retrace_repeats_them(tmp_path):
    same = box(button("Same"))
    data = _round([same, same], [same])
    js = """
    await settle();
    const paths = (q, o) => JSON.stringify(marksOf(drawing(q, o)).map((m) => m.paths));
    const a = paths(0, 0);
    const b = paths(0, 1);
    document.querySelectorAll('nav button')[1].click();
    await settle();
    return {a, b, c: paths(1, 0)};
    """
    got = _run(tmp_path, data, js)
    assert len({got["a"], got["b"], got["c"]}) == 3


@pytest.mark.parametrize("payload", HOSTILE)
def test_hostile_labels_in_a_layout_show_as_literal_text(tmp_path, payload):
    layout = box(
        {"text": payload}, button(payload), {"pill": payload}, {"field": payload}, {"image": payload}
    )
    js = """
    await settle();
    const d = drawing(0, 0);
    return {
      labels: [...d.querySelectorAll('.pen-label')].map((e) => e.textContent),
      pwned: typeof window.pwned,
      injected: d.querySelectorAll('script, img').length,
      marks: marksOf(d).length,
    };
    """
    got = _run(tmp_path, _round([layout]), js)
    assert got["labels"] == [payload] * 5
    assert got["pwned"] == "undefined"
    assert got["injected"] == 0
    assert got["marks"] == 5


@pytest.mark.parametrize("payload", HOSTILE)
def test_hostile_text_in_an_unreadable_block_shows_as_literal_text(tmp_path, payload):
    layout = box({payload: payload})
    js = """
    await settle();
    const d = drawing(0, 0);
    return {
      message: messageOf(d),
      pwned: typeof window.pwned,
      injected: d.querySelectorAll('script, img').length,
    };
    """
    got = _run(tmp_path, _round([layout]), js)
    quoted = json.dumps({payload: payload}).replace('": "', '": "')
    assert got["message"] == [
        "Can't draw this layout:",
        f"box › 0 › {quoted}",
        f"{json.dumps(payload)} is not a block kind.",
    ]
    assert got["pwned"] == "undefined"
    assert got["injected"] == 0
