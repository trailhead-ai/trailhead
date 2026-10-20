"""Behavioural tests for the scratch-pad kit's flow drawing.

Every test builds a real pad with ``scratch_pad.build_pad`` from the real kit, opens the built
round folder in a headless browser through the probe harness, and asserts on the strokes the
pen traced and the text the browser measured and set.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import math
import random
import tempfile
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


LIB = r"""
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (predicate, ms = 40000) => {
  for (let waited = 0; waited < ms && !predicate(); waited += 50) await sleep(50);
};
const drawing = (q, o) =>
  document.querySelector(`.pad-drawing[data-question="${q}"][data-option="${o}"]`);
const inkOf = (d) => d.querySelector('svg.pad-ink');
const points = (d) => {
  const n = (d.match(/-?\d+(\.\d+)?/g) || []).map(Number);
  const out = [];
  for (let i = 0; i + 1 < n.length; i += 2) out.push([n[i], n[i + 1]]);
  return out;
};
const pathsOf = (g) => [...g.querySelectorAll('path')].map((p) => points(p.getAttribute('d')));
const boundsOf = (paths) => {
  const all = paths.flat();
  const xs = all.map((p) => p[0]);
  const ys = all.map((p) => p[1]);
  return {x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys)};
};
const stepsOf = (d) =>
  [...inkOf(d).querySelectorAll('g[data-kind]')]
    .filter((g) => !['arrow', 'loop'].includes(g.getAttribute('data-kind')))
    .map((g) => {
      const paths = pathsOf(g);
      const b = boundsOf(paths);
      return {
        id: g.getAttribute('data-step-id'),
        kind: g.getAttribute('data-kind'),
        paths,
        b,
        cx: (b.x0 + b.x1) / 2,
        cy: (b.y0 + b.y1) / 2,
        texts: [...g.querySelectorAll('text')].map((t) => {
          const bb = t.getBBox();
          return {text: t.textContent, x0: bb.x, x1: bb.x + bb.width, y0: bb.y, y1: bb.y + bb.height};
        }),
      };
    });
const arrowsOf = (d) =>
  [...inkOf(d).querySelectorAll('g[data-kind="arrow"]')].map((g) => ({
    index: Number(g.getAttribute('data-arrow-index')),
    paths: pathsOf(g),
  }));
const loopsOf = (d) =>
  [...inkOf(d).querySelectorAll('g[data-kind="loop"]')].map((g) => {
    const text = g.querySelector('text');
    const halo = g.querySelector('rect');
    const kids = [...g.children];
    return {
      index: Number(g.getAttribute('data-arrow-index')),
      paths: pathsOf(g),
      dashes: [...g.querySelectorAll('path')].map((p) => getComputedStyle(p).strokeDasharray),
      label: text
        ? {text: text.textContent, y: Number(text.getAttribute('y')), x: Number(text.getAttribute('x'))}
        : null,
      halo: halo
        ? {y: halo.y.baseVal.value, h: halo.height.baseVal.value, under: kids.indexOf(halo) < kids.indexOf(text)}
        : null,
    };
  });
const kindsOf = (d) =>
  Object.fromEntries([...inkOf(d).querySelectorAll('g[data-arrow-index]')]
    .map((g) => [g.getAttribute('data-arrow-index'), g.getAttribute('data-kind')]));
const messageOf = (d) => {
  const m = d.querySelector('.pen-message');
  return m ? [...m.querySelectorAll('p')].map((p) => p.textContent) : null;
};
const marksOf = (d) =>
  [...inkOf(d).querySelectorAll('g')].map((g) => {
    const bb = g.getBBox();
    const index = Number(g.getAttribute('data-pen-index'));
    const el = d.querySelector(`[data-pen-index="${index}"]`);
    const base = inkOf(d).getBoundingClientRect();
    const r = el.getBoundingClientRect();
    return {
      kind: g.getAttribute('data-kind'),
      box: {x: bb.x, y: bb.y, w: bb.width, h: bb.height},
      element: {x: r.left - base.left, y: r.top - base.top, w: r.width, h: r.height},
    };
  });
const flowsOf = (q, o) => {
  const d = drawing(q, o);
  return {steps: stepsOf(d), arrows: arrowsOf(d), message: messageOf(d)};
};
const everyPath = (d) => [...inkOf(d).querySelectorAll('path')].map((p) => p.getAttribute('d'));
const arrowDetail = (d) =>
  [...inkOf(d).querySelectorAll('g[data-kind="arrow"]')].map((g) => {
    const kids = [...g.children];
    const text = g.querySelector('text');
    const halo = g.querySelector('rect');
    const tb = text ? text.getBBox() : null;
    return {
      index: Number(g.getAttribute('data-arrow-index')),
      dashes: [...g.querySelectorAll('path')].map((p) => getComputedStyle(p).strokeDasharray),
      shaft: pathsOf(g)[0],
      label: text
        ? {
            text: text.textContent,
            at: [Number(text.getAttribute('x')), Number(text.getAttribute('y'))],
            x: tb.x, y: tb.y, w: tb.width, h: tb.height,
          }
        : null,
      halo: halo
        ? {
            x: halo.x.baseVal.value, y: halo.y.baseVal.value, w: halo.width.baseVal.value, h: halo.height.baseVal.value,
            fill: getComputedStyle(halo).fill, under: kids.indexOf(halo) < kids.indexOf(text),
          }
        : null,
    };
  });
const ready = () => until(() =>
  document.documentElement.hasAttribute('data-pad-ready') &&
  document.querySelector('svg.pad-ink path, .pen-message'));
"""

TOL = 4.0


def _option(i, drawing):
    base = {"name": f"opt {i}", "cost": "cost", "pick": i == 0}
    return {**base, **(drawing if isinstance(drawing, dict) else {"flow": drawing})}


def _round(*questions):
    return {
        "title": "Flow",
        "questions": [
            {"prompt": f"Q{qi}", "options": [_option(oi, d) for oi, d in enumerate(options)]}
            for qi, options in enumerate(questions)
        ],
    }


def _build(tmp_path, data):
    home = Path(tempfile.mkdtemp(dir=tmp_path))
    round_path = home / "round.json"
    round_path.write_text(json.dumps(data))
    env = {"OUTPOST_STATE_DIR": str(home / "state")}
    _, pad = scratch_pad.build_pad(round_path, "demo", env=env)
    return pad / "round-1"


def _run(tmp_path, data, js, **kwargs):
    return ch.run_probe(_build(tmp_path, data), LIB + "await ready();\n" + js, **kwargs)


ALL = "return {steps: stepsOf(drawing(0, 0)), arrows: arrowsOf(drawing(0, 0))};"


def _by_id(steps):
    return {s["id"]: s for s in steps}


def _head(arrow):
    """The tip of an arrow's first head stroke and the far ends of its two head strokes."""
    paths = arrow["paths"]
    tip = paths[2][-1]
    return tip, [paths[2][0], paths[4][0]]


CHAIN = "a[One] --> b[Two] --> c[Three]"


def test_an_lr_flow_draws_steps_left_to_right_with_arrows_into_left_edges(tmp_path):
    got = _run(tmp_path, _round([f"flowchart LR\n  {CHAIN}"]), ALL)
    steps = got["steps"]
    assert [s["id"] for s in steps] == ["a", "b", "c"]
    assert [s["kind"] for s in steps] == ["box"] * 3
    xs = [s["cx"] for s in steps]
    assert xs == sorted(xs) and xs[2] - xs[0] > 100
    assert max(s["cy"] for s in steps) - min(s["cy"] for s in steps) <= TOL
    assert [a["index"] for a in got["arrows"]] == [0, 1]
    for arrow, target in zip(got["arrows"], steps[1:]):
        tip, backs = _head(arrow)
        assert abs(tip[0] - target["b"]["x0"]) <= TOL
        assert target["b"]["y0"] < tip[1] < target["b"]["y1"]
        assert all(back[0] < tip[0] - 3 for back in backs)
        assert backs[0][1] < tip[1] < backs[1][1] or backs[1][1] < tip[1] < backs[0][1]


def test_a_td_flow_draws_steps_top_down_with_arrows_into_top_edges_and_aliases_match(tmp_path):
    flows = [f"{head}\n  {CHAIN}" for head in ("flowchart TD", "graph TD", "flowchart TB", "graph TB")]
    js = "return [0, 1, 2, 3].map((o) => flowsOf(0, o));"
    got = _run(tmp_path, _round(flows), js)
    first = got[0]
    ys = [s["cy"] for s in first["steps"]]
    assert ys == sorted(ys) and ys[2] - ys[0] > 80
    assert max(s["cx"] for s in first["steps"]) - min(s["cx"] for s in first["steps"]) <= TOL
    for arrow, target in zip(first["arrows"], first["steps"][1:]):
        tip, backs = _head(arrow)
        assert abs(tip[1] - target["b"]["y0"]) <= TOL
        assert target["b"]["x0"] < tip[0] < target["b"]["x1"]
        assert all(back[1] < tip[1] - 3 for back in backs)
    for other in got[1:]:
        assert len(other["steps"]) == 3 and len(other["arrows"]) == 2
        for mine, theirs in zip(first["steps"], other["steps"]):
            assert abs(mine["cx"] - theirs["cx"]) <= TOL and abs(mine["cy"] - theirs["cy"]) <= TOL


def _corner(step):
    b = step["b"]
    return min(
        min(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in p)
        for p in step["paths"]
        for cx, cy in [(b["x0"], b["y0"])]
    )


def test_each_shape_token_is_its_own_outline(tmp_path):
    flows = [
        "flowchart LR\n a[Label]",
        "flowchart LR\n a(Label)",
        "flowchart LR\n a([Label])",
        "flowchart LR\n a{Label}",
    ]
    js = "return [0, 1, 2, 3].map((o) => stepsOf(drawing(0, o))[0]);"
    box, rounded, pill, decision = _run(tmp_path, _round(flows), js)
    assert [box["kind"], rounded["kind"], pill["kind"], decision["kind"]] == ["box", "round", "pill", "decision"]
    assert _corner(box) < 2.2
    assert _corner(rounded) > 3.3
    assert _corner(pill) > _corner(rounded) + 1
    half = (pill["b"]["y1"] - pill["b"]["y0"]) / 2
    left = [p for path in pill["paths"] for p in path if p[0] < pill["b"]["x0"] + half * 0.3]
    assert all(
        abs(((p[0] - (pill["b"]["x0"] + half)) ** 2 + (p[1] - pill["cy"]) ** 2) ** 0.5 - half) <= 2
        for p in left
        if p[0] < pill["b"]["x0"] + half - 1
    )
    b = decision["b"]
    extremes = {
        "left": [p for path in decision["paths"] for p in path if abs(p[0] - b["x0"]) <= 1.2],
        "right": [p for path in decision["paths"] for p in path if abs(p[0] - b["x1"]) <= 1.2],
        "top": [p for path in decision["paths"] for p in path if abs(p[1] - b["y0"]) <= 1.2],
        "bottom": [p for path in decision["paths"] for p in path if abs(p[1] - b["y1"]) <= 1.2],
    }
    assert all(extremes[side] for side in extremes)
    assert all(abs(p[1] - decision["cy"]) <= TOL for p in extremes["left"] + extremes["right"])
    assert all(abs(p[0] - decision["cx"]) <= TOL for p in extremes["top"] + extremes["bottom"])
    assert _corner(decision) > 8


def test_every_outline_encloses_its_label_and_no_two_outlines_overlap(tmp_path):
    flow = (
        "flowchart LR\n"
        "  s[Start here] --> d{Is the very long question about everything visual?}\n"
        "  d --> p([Pill with a rather long label that must wrap onto lines because it keeps going on"
        " and on about nothing in particular until a second line is certainly needed])\n"
        "  d --> r(Rounded)\n"
        "  s --> x[Supercalifragilisticexpialidocious]"
    )
    got = _run(tmp_path, _round([flow]), ALL)
    steps = got["steps"]
    assert len(steps) == 5
    for step in steps:
        assert step["texts"], step["id"]
        assert any(t["text"].strip() for t in step["texts"])
        b = step["b"]
        for t in step["texts"]:
            assert t["x1"] - t["x0"] > 5
            if step["kind"] == "decision":
                hw, hh = (b["x1"] - b["x0"]) / 2, (b["y1"] - b["y0"]) / 2
                for x in (t["x0"], t["x1"]):
                    for y in (t["y0"], t["y1"]):
                        assert abs(x - step["cx"]) / hw + abs(y - step["cy"]) / hh <= 1.0, step["id"]
            else:
                assert b["x0"] <= t["x0"] and t["x1"] <= b["x1"], step["id"]
                assert b["y0"] <= t["y0"] and t["y1"] <= b["y1"], step["id"]
    for i, one in enumerate(steps):
        for two in steps[i + 1 :]:
            a, b = one["b"], two["b"]
            assert a["x1"] <= b["x0"] or b["x1"] <= a["x0"] or a["y1"] <= b["y0"] or b["y1"] <= a["y0"], (
                one["id"],
                two["id"],
            )
    wrapped = [s for s in steps if s["id"] == "p"][0]
    assert len(wrapped["texts"]) > 1


def _crosses(path, rect, grow=-2.0):
    x0, x1, y0, y1 = rect["x0"] - grow, rect["x1"] + grow, rect["y0"] - grow, rect["y1"] + grow
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        for k in range(61):
            t = k / 60
            x, y = ax + (bx - ax) * t, ay + (by - ay) * t
            if x0 < x < x1 and y0 < y < y1:
                return True
    return False


def test_an_arrow_skipping_a_layer_runs_through_a_waypoint_and_crosses_no_step(tmp_path):
    flow = "flowchart LR\n  a[A] --> c[C]\n  a --> b[B] --> c"
    got = _run(tmp_path, _round([flow]), ALL)
    steps = _by_id(got["steps"])
    assert steps["a"]["cx"] < steps["b"]["cx"] < steps["c"]["cx"]
    assert abs(steps["b"]["cx"] - (steps["a"]["cx"] + steps["c"]["cx"]) / 2) < 60
    skip = [a for a in got["arrows"] if a["index"] == 0][0]
    shaft = skip["paths"][0]
    b = steps["b"]["b"]
    assert any(y > b["y1"] + 2 or y < b["y0"] - 2 for _, y in shaft), "the skipping arrow must leave b's row"
    for step in steps.values():
        assert not _crosses(shaft, step["b"]), step["id"]
    tip, _ = _head(skip)
    assert abs(tip[0] - steps["c"]["b"]["x0"]) <= TOL


def test_a_step_mentioned_bare_after_its_declaration_is_one_step(tmp_path):
    flows = [
        "flowchart LR\n  a[One]\n  b[Two]\n  a --> b\n  b --> c\n  c[Three]",
        "flowchart LR\n  a[One] --> b\n  a[One] --> c",
    ]
    js = "return [0, 1].map((o) => flowsOf(0, o));"
    first, second = _run(tmp_path, _round(flows), js)
    assert [(s["id"], s["texts"][0]["text"]) for s in first["steps"]] == [("a", "One"), ("b", "Two"), ("c", "Three")]
    assert len(first["arrows"]) == 2
    assert [s["id"] for s in second["steps"]] == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("flow", "line"),
    [
        ("flowchart LR\n  a[One] --> b\n  a[Two] --> b", "a[Two] --> b"),
        ("flowchart LR\n  a[One]\n\n  a(One)", "a(One)"),
        ("flowchart LR\n  a[One]\n  b\n  b{Two}\n  b[Three]", "b[Three]"),
    ],
    ids=["label", "shape", "second-redeclaration"],
)
def test_a_step_redeclared_differently_names_the_second_line(tmp_path, flow, line):
    js = "return {message: messageOf(drawing(0, 0)), sibling: stepsOf(drawing(0, 1)).length};"
    got = _run(tmp_path, _round([flow, "flowchart LR\n a --> b"]), js)
    lineno = flow.split("\n").index("  " + line) + 1
    assert got["message"][:2] == ["Can't draw this flow:", f"line {lineno}: {line}"]
    assert len(got["message"]) == 4 and got["message"][2]
    assert got["message"][3] == REDRAW
    assert got["sibling"] == 2


LONG = "flowchart LR\n  " + " --> ".join(f"n{i}[Step number {i}]" for i in range(10))
MEDIUM = "flowchart LR\n  " + " --> ".join(f"n{i}[Step {i}]" for i in range(5))
SHORT = "flowchart LR\n  a[One] --> b[Two]"

WIDTHS = """
const d = drawing(0, 0);
const svg = inkOf(d);
const natural = svg.viewBox.baseVal.width;
return {
  natural,
  svg: svg.getBoundingClientRect().width,
  card: d.clientWidth,
  scrolls: d.scrollWidth > d.clientWidth + 1,
  overflowX: getComputedStyle(d).overflowX,
  paths: everyPath(d),
};
"""


@pytest.mark.parametrize(
    ("flow", "regime"), [(SHORT, "natural"), (MEDIUM, "fitted"), (LONG, "floor")], ids=["short", "medium", "long"]
)
def test_a_flow_scales_down_to_its_card_but_never_up_nor_below_point_six(tmp_path, flow, regime):
    got = _run(tmp_path, _round([flow]), WIDTHS)
    scale = got["svg"] / got["natural"]
    expected = max(0.6, min(1.0, got["card"] / got["natural"]))
    assert scale == pytest.approx(expected, abs=0.01)
    assert got["scrolls"] == (got["natural"] * 0.6 > got["card"] + 1)
    if got["scrolls"]:
        assert got["overflowX"] in ("auto", "scroll")
    if regime == "natural":
        assert scale == pytest.approx(1.0, abs=0.001) and not got["scrolls"]
    elif regime == "fitted":
        assert 0.61 < scale < 0.99 and not got["scrolls"]
    else:
        assert scale == pytest.approx(0.6, abs=0.001) and got["scrolls"] and got["natural"] > 2 * got["card"]


THREE = "flowchart LR\n  a[Step one] --> b[Step two] --> c[Step three]"


def test_the_same_flow_scales_with_the_window_but_its_path_data_never_changes(tmp_path):
    data = _round([THREE])
    got = {w: _run(tmp_path, data, WIDTHS, window_size=(w, 900)) for w in (500, 900, 1400)}
    assert got[500]["svg"] < got[1400]["svg"] - 50
    for g in got.values():
        assert g["svg"] / g["natural"] == pytest.approx(max(0.6, min(1.0, g["card"] / g["natural"])), abs=0.01)
        assert g["paths"] == got[1400]["paths"] and g["paths"]


def _pairs(paths):
    return [(paths[i], paths[i + 1]) for i in range(0, len(paths), 2)]


def test_every_flow_stroke_is_two_wobbling_passes_beside_a_layout(tmp_path):
    flow = "flowchart LR\n  a[One] --> b{Two}\n  b --> c([Three])\n  b --> d(Four)"
    layout = {"layout": {"box": [{"button": "Save"}]}}
    js = """
    const out = {};
    for (const o of [0, 1, 2]) {
      out[o] = [...inkOf(drawing(0, o)).querySelectorAll('g')]
        .map((g) => ({kind: g.getAttribute('data-kind'), paths: pathsOf(g)}));
    }
    return out;
    """
    got = _run(tmp_path, _round([flow, layout, flow]), js)
    for o in ("0", "1", "2"):
        assert got[o]
        for group in got[o]:
            assert len(group["paths"]) % 2 == 0 and len(group["paths"]) >= 2
            for first, second in _pairs(group["paths"]):
                assert first != second, (o, group["kind"])
                assert len(first) == len(second)
    assert [g["kind"] for g in got["0"]] == ["box", "decision", "pill", "round", "arrow", "arrow", "arrow"]
    assert all(len(g["paths"]) == 6 for g in got["0"] if g["kind"] == "arrow")
    assert got["0"] != got["2"]
    assert [len(g["paths"]) for g in got["0"]] == [len(g["paths"]) for g in got["2"]]


def test_opening_the_same_pad_twice_gives_identical_path_data_for_every_drawing(tmp_path):
    layout = {"layout": {"box": [{"button": "Save"}, {"pill": "Tag"}]}}
    flow = "flowchart LR\n  a[One] --> b{Two}\n  a --> c\n  b --> c"
    data = _round([flow, layout], ["flowchart TD\n  x --> y", layout])
    js = """
    const out = [];
    for (const q of [0, 1]) {
      if (q === 1) {
        document.querySelectorAll('nav button')[1].click();
        await until(() => drawing(1, 1) && everyPath(drawing(1, 1)).length > 0);
      }
      for (const o of [0, 1]) out.push(everyPath(drawing(q, o)));
    }
    return out;
    """
    one = _run(tmp_path, data, js)
    two = _run(tmp_path, data, js)
    assert one == two
    assert all(one) and len({json.dumps(x) for x in one}) == 4


MESSAGE = """
return {
  message: messageOf(drawing(0, 0)),
  svgs: drawing(0, 0).querySelectorAll('svg').length,
  sibling: marksOf(drawing(0, 1)).map((m) => m.kind),
};
"""
SIBLING = {"layout": {"box": [{"button": "fine"}]}}
REDRAW = "Ask the session to redraw this option."


@pytest.mark.parametrize(
    ("flow", "line", "reason"),
    [
        ("flowchart LR\n  a --> b\n  subgraph x", "line 3: subgraph x", "subgraph"),
        ("a --> b", "line 1: a --> b", "flowchart"),
        ("\n\nflowchart RL\n  a --> b", "line 3: flowchart RL", "RL"),
        ("flowchart LR\n  a((round)) --> b", "line 2: a((round)) --> b", "step"),
    ],
    ids=["subgraph", "no-header", "bad-direction", "other-shape"],
)
def test_a_flow_that_cannot_be_drawn_shows_the_line_its_reason_and_the_redraw_request(tmp_path, flow, line, reason):
    got = _run(tmp_path, _round([flow, SIBLING]), MESSAGE)
    assert len(got["message"]) == 4
    assert got["message"][0] == "Can't draw this flow:"
    assert got["message"][1] == line
    assert reason in got["message"][2]
    assert got["message"][3] == REDRAW
    assert got["svgs"] == 0
    assert got["sibling"] == ["box", "button"]


@pytest.mark.parametrize(
    "flow", ["", "flowchart LR", "\n  \n flowchart TD \n\n"], ids=["empty", "header-alone", "blank-lines"]
)
def test_an_empty_flow_shows_the_whole_flow_message(tmp_path, flow):
    got = _run(tmp_path, _round([flow, SIBLING]), MESSAGE)
    assert got["message"] == ["Can't draw this flow:", "the whole flow", "The flow has no steps.", REDRAW]
    assert got["sibling"] == ["box", "button"]


@pytest.mark.parametrize("payload", ["<script>window.pwned=1</script>", "<img src=x onerror=window.pwned=1>"])
def test_markup_in_labels_and_in_refused_lines_shows_as_text(tmp_path, payload):
    ok = f'flowchart LR\n  a["{payload}"] --> b'
    bad = f"flowchart LR\n  subgraph {payload}"
    js = """
    const d = drawing(0, 0);
    return {
      labels: stepsOf(d).map((s) => s.texts.map((t) => t.text)),
      message: messageOf(drawing(0, 1)),
      pwned: typeof window.pwned,
      injected: document.querySelectorAll('#pad script, #pad img').length,
    };
    """
    got = _run(tmp_path, _round([ok, bad]), js)
    assert "".join(got["labels"][0]).replace(" ", "") == payload.replace(" ", "")
    assert got["message"][1] == f"line 2: subgraph {payload}"
    assert got["pwned"] == "undefined" and got["injected"] == 0


def test_step_ids_named_like_object_members_draw_as_ordinary_steps(tmp_path):
    flow = "flowchart LR\n  __proto__[Proto] --> constructor --> toString --> hasOwnProperty"
    js = """
    const d = drawing(0, 0);
    return {
      steps: stepsOf(d).map((s) => [s.id, s.texts.map((t) => t.text).join(' ')]),
      arrows: arrowsOf(d).length,
      polluted: ({}).polluted === undefined,
      keys: Object.keys(Object.prototype).length,
      toString: Object.prototype.toString.call({}) === '[object Object]',
      ctor: ({}).constructor === Object && typeof ({}).hasOwnProperty === 'function',
    };
    """
    got = _run(tmp_path, _round([flow]), js)
    assert got["steps"] == [
        ["__proto__", "Proto"],
        ["constructor", "constructor"],
        ["toString", "toString"],
        ["hasOwnProperty", "hasOwnProperty"],
    ]
    assert got["arrows"] == 3
    assert got["polluted"] and got["keys"] == 0 and got["toString"] and got["ctor"]


def test_each_flow_is_traced_beside_a_layout_with_their_own_seeds(tmp_path):
    flow = "flowchart LR\n  a[One] --> b[Two]"
    js = "return [0, 1, 2].map((o) => everyPath(drawing(0, o)));"
    got = _run(tmp_path, _round([flow, flow, SIBLING]), js)
    assert got[0] != got[1] and got[0] and got[2]



def test_a_dashed_arrow_draws_both_shaft_passes_dashed_and_a_plain_arrow_draws_none(tmp_path):
    flows = [
        "flowchart LR\n  a[One] --> b[Two]",
        "flowchart LR\n  a[One] -.-> b[Two]",
        "flowchart LR\n  a[One] -.-> b[Two] --> c[Three]",
    ]
    js = "return [0, 1, 2].map((o) => arrowDetail(drawing(0, o)).map((a) => a.dashes));"
    plain, dashed, mixed = _run(tmp_path, _round(flows), js)
    assert plain == [["none"] * 6]
    assert [d != "none" for d in dashed[0]] == [True, True, False, False, False, False]
    assert [d != "none" for d in mixed[0]] == [True, True, False, False, False, False]
    assert mixed[1] == ["none"] * 6


LABEL_FLOW = (
    "flowchart LR\n"
    "  a[A] {arrow} c[End]\n"
    "  w[A much wider starting step] --> b[Middle] --> c\n"
    "  a --> b"
)
LABEL_FORMS = ["-->|go|", "-- go -->", "-.->|go|", "-. go .->"]


def test_each_labelled_arrow_form_sets_its_label_at_the_middle_of_the_first_leg_on_a_halo(tmp_path):
    flows = [LABEL_FLOW.format(arrow=form) for form in LABEL_FORMS]
    js = """
    const body = getComputedStyle(document.body).backgroundColor;
    return {
      body,
      steps: Object.fromEntries(stepsOf(drawing(0, 0)).map((s) => [s.id, s])),
      forms: [0, 1, 2, 3].map((o) => arrowDetail(drawing(0, o)).filter((a) => a.index === 0)[0]),
      unlabelled: [0, 1, 2, 3]
        .map((o) => arrowDetail(drawing(0, o)).filter((a) => a.index !== 0 && a.label))
        .flat().length,
    };
    """
    got = _run(tmp_path, _round(flows), js)
    first = got["forms"][0]
    assert first["label"]["text"] == "go"
    a, wide = got["steps"]["a"], got["steps"]["w"]
    cx = first["label"]["x"] + first["label"]["w"] / 2
    cy = first["label"]["y"] + first["label"]["h"] / 2
    assert wide["b"]["x1"] - a["b"]["x1"] > 60, "the first leg runs from a's edge to the end of its column"
    assert abs(cx - (a["b"]["x1"] + wide["b"]["x1"]) / 2) <= TOL
    assert abs(cy - a["cy"]) <= TOL
    for form in got["forms"]:
        assert form["label"]["text"] == "go"
        assert form["label"]["at"] == first["label"]["at"]
        assert form["label"]["w"] == pytest.approx(first["label"]["w"], abs=0.5)
        halo, label = form["halo"], form["label"]
        assert halo["under"]
        assert halo["fill"] == got["body"]
        assert halo["x"] <= label["x"] + 0.01 and halo["y"] <= label["y"] + 0.01
        assert halo["x"] + halo["w"] >= label["x"] + label["w"]
        assert halo["y"] + halo["h"] >= label["y"] + label["h"]
    assert got["unlabelled"] == 0


def test_quoted_labels_on_steps_and_arrows_keep_their_brackets(tmp_path):
    flow = 'flowchart LR\n  a["text (with) brackets"] -->|"say (it)"| b["[boxed]"]'
    js = """
    const d = drawing(0, 0);
    return {
      steps: stepsOf(d).map((s) => s.texts.map((t) => t.text)),
      labels: arrowDetail(d).map((a) => a.label && a.label.text),
    };
    """
    got = _run(tmp_path, _round([flow]), js)
    assert got["steps"] == [["text (with) brackets"], ["[boxed]"]]
    assert got["labels"] == ["say (it)"]


PLAIN = "flowchart LR\n  a[One] --> b[Two]\n  b --> c[Three]"
_baseline = {}


def _plain_paths(tmp_path):
    if "paths" not in _baseline:
        _baseline["paths"] = _run(tmp_path, _round([PLAIN]), "return everyPath(drawing(0, 0));")
    return _baseline["paths"]


def test_styling_lines_between_the_lines_of_a_flow_change_none_of_its_strokes(tmp_path):
    styled = (
        "flowchart LR\n"
        "  %%{init: {'theme': 'forest'}}%%\n"
        "  classDef hot fill:#f96\n"
        "  a[One] --> b[Two]\n"
        "  style a fill:#f9f\n"
        "  linkStyle 0 stroke:#ff3\n"
        "  class b hot\n"
        "  b --> c[Three]\n"
    )
    assert _run(tmp_path, _round([styled]), "return everyPath(drawing(0, 0));") == _plain_paths(tmp_path)


@pytest.mark.parametrize("mark", ["someclass", "accent"])
def test_a_class_mark_on_a_step_changes_none_of_its_strokes_and_accent_is_recorded(tmp_path, mark):
    marked = f"flowchart LR\n  a[One]:::{mark} --> b[Two]\n  b --> c:::other\n  c[Three]"
    js = """
    const model = PadFlow.parse(%s);
    return {paths: everyPath(drawing(0, 0)), accent: [...model.steps.values()].map((s) => s.accent)};
    """ % json.dumps(marked)
    got = _run(tmp_path, _round([marked]), js)
    assert got["paths"] == _plain_paths(tmp_path)
    assert got["accent"] == [mark == "accent", False, False]


REFUSED = [
    ("flowchart LR\n  %% a note\n  a --> b", [(2, "%% a note", "comment")]),
    ("flowchart RL\n  a --> b", [(1, "flowchart RL", "RL")]),
    ("graph BT\n  a --> b", [(1, "graph BT", "BT")]),
    ("flowchart LR\n  subgraph one\n  a --> b\n  end", [(2, "subgraph one", "groups"), (4, "end", "groups")]),
    ("flowchart LR\n  a & b --> c", [(2, "a & b --> c", "&")]),
    ("flowchart LR\n  a ==> b", [(2, "a ==> b", "Thick")]),
    ("flowchart LR\n  a --- b", [(2, "a --- b", "Open")]),
    ("flowchart LR\n  a --o b", [(2, "a --o b", "Circle-ended")]),
    ("flowchart LR\n  a --x b", [(2, "a --x b", "Cross-ended")]),
    ("flowchart LR\n  a <--> b", [(2, "a <--> b", "Two-way")]),
    ("flowchart LR\n  a((round)) --> b", [(2, "a((round)) --> b", "Circle steps")]),
    ("flowchart LR\n  a{{six}} --> b", [(2, "a{{six}} --> b", "Hexagon steps")]),
    ("flowchart LR\n  a[(store)] --> b", [(2, "a[(store)] --> b", "Database steps")]),
    ("flowchart LR\n  a>flag] --> b", [(2, "a>flag] --> b", "Flag steps")]),
    ("flowchart LR\n  a[/slant/] --> b", [(2, "a[/slant/] --> b", "Slanted steps")]),
    ("flowchart LR\n  a --> b\n  click a callback", [(3, "click a callback", "click")]),
    ("flowchart LR\n  a ~~~ b", [(2, "a ~~~ b", "not an arrow")]),
    ("flowchart LR\n  a -->", [(2, "a -->", "needs a step")]),
]

ALL_MESSAGES = """
return [...document.querySelectorAll('.pad-drawing')].map(
  (d) => ({message: messageOf(d), steps: inkOf(d) ? stepsOf(d).length : 0}));
"""


def test_each_refused_construct_names_its_own_line_with_a_reason_specific_to_it(tmp_path):
    flows = [flow for flow, _ in REFUSED] + ["flowchart LR\n  a --> b", SIBLING]
    got = _run(tmp_path, _round(flows), ALL_MESSAGES)
    reasons = []
    for (flow, problems), result in zip(REFUSED, got):
        message = result["message"]
        assert message is not None, flow
        expected_len = 2 + 2 * len(problems)
        assert len(message) == expected_len, (flow, message)
        assert message[0] == "Can't draw this flow:", flow
        assert message[-1] == REDRAW, flow
        for k, (lineno, text, reason) in enumerate(problems):
            assert message[1 + 2 * k] == f"line {lineno}: {text}", (flow, message)
            assert reason in message[2 + 2 * k], (flow, message)
            reasons.append(message[2 + 2 * k])
    assert "This pen does not draw this line." not in reasons
    assert len(set(reasons)) >= len(REFUSED)
    assert got[-2]["message"] is None and got[-2]["steps"] == 2
    assert got[-1]["message"] is None


def test_a_flow_lists_every_broken_line_up_to_five_then_says_how_many_more(tmp_path):
    def flow(count):
        return "flowchart LR\n  a --> b\n" + "\n".join(f"  click n{i} cb" for i in range(count))

    got = _run(tmp_path, _round([flow(3), flow(5), flow(6), flow(7)]), ALL_MESSAGES)
    three, five, six, seven = (g["message"] for g in got)
    assert three[1::2][:3] == [f"line {3 + i}: click n{i} cb" for i in range(3)]
    assert len(three) == 2 + 2 * 3 and three[-1] == REDRAW
    assert len(five) == 2 + 2 * 5 and five[-1] == REDRAW and not any(p.startswith("and ") for p in five)
    assert len(six) == 2 + 2 * 5 + 1 and six[-2] == "and 1 more" and six[-1] == REDRAW
    assert len(seven) == 2 + 2 * 5 + 1 and seven[-2] == "and 2 more" and seven[-1] == REDRAW
    assert seven[9] == "line 7: click n4 cb"


def _chain(count, extra=()):
    lines = [f"s{i} --> s{i + 1}" for i in range(count - 1)]
    return lines + list(extra)


def _flow(lines, pad_to=None):
    lines = ["flowchart LR"] + list(lines)
    if pad_to:
        lines += [""] * (pad_to - len(lines))
    return "\n".join(lines)


def _waypoint_flow(extra_short):
    longs = ["s0 --> s59"] * 34 + ["s0 --> s29"] + ["s0 --> s2"] * extra_short
    return _flow(_chain(60, longs))


LIMITS = [
    ("400 lines", _flow(["a --> b"], pad_to=400), None),
    ("401 lines", _flow(["a --> b"], pad_to=400) + "\nsubgraph x", "The flow has 401 lines; at most 400 are drawn."),
    ("1000 chars", _flow(["a --> b" + " " * 993]), None),
    ("1001 chars", _flow(["a --> b" + " " * 994, "subgraph x"]), "Line 2 has 1001 characters; at most 1000 are drawn."),
    ("60 steps", _flow(_chain(60)), None),
    ("61 steps", _flow(_chain(61, ["subgraph x"])), "The flow has 61 steps; at most 60 are drawn."),
    ("120 arrows", _flow(["a --> b"] * 120), None),
    ("121 arrows", _flow(["a --> b"] * 121 + ["subgraph x"]), "The flow has 121 arrows; at most 120 are drawn."),
    ("2000 waypoints", _waypoint_flow(0), None),
    ("2001 waypoints", _waypoint_flow(1), "The flow's arrows pass 2001 waypoints; at most 2000 are drawn."),
    (
        "lines before chars before steps",
        _flow(_chain(61) + ["a --> b" + " " * 994], pad_to=401),
        "The flow has 401 lines; at most 400 are drawn.",
    ),
    (
        "chars before steps",
        _flow(["a --> b" + " " * 994] + _chain(61)),
        "Line 2 has 1001 characters; at most 1000 are drawn.",
    ),
    (
        "steps before arrows",
        _flow(_chain(61, ["s0 --> s1"] * 70)),
        "The flow has 61 steps; at most 60 are drawn.",
    ),
]


def test_each_limit_draws_at_the_limit_and_refuses_one_over_naming_the_number_found(tmp_path):
    flows = [flow for _, flow, _ in LIMITS] + [SIBLING]
    got = _run(tmp_path, _round(flows), ALL_MESSAGES, timeout=120)
    for (name, _, reason), result in zip(LIMITS, got):
        message = result["message"]
        if reason is None:
            assert message is None and result["steps"] >= 2, name
        else:
            assert message == ["Can't draw this flow:", "the whole flow", reason, REDRAW], (name, message)
    assert got[-1]["message"] is None


def test_a_flow_that_closes_a_loop_draws_it_and_lists_only_the_other_broken_lines(tmp_path):
    loop = "flowchart LR\n  a --> b\n  b --> a"
    broken = "flowchart LR\n  a --> b\n  subgraph x\n  b --> a"
    js = """
    return [0, 1].map((o) => {
      const d = drawing(0, o);
      return {message: messageOf(d), loops: inkOf(d) ? loopsOf(d).length : 0};
    });
    """
    drawn, listed = _run(tmp_path, _round([loop, broken]), js)
    assert drawn["message"] is None and drawn["loops"] == 1
    assert listed["message"][1] == "line 3: subgraph x" and len(listed["message"]) == 4


def _lane(loop, axis):
    """The far side of the lane a loop back runs along: its greatest y (LR) or x (TD)."""
    return max(p[axis] for p in loop["paths"][0])


LOOP_PROBE = """
const d = drawing(0, %d);
return {steps: stepsOf(d), loops: loopsOf(d), kinds: kindsOf(d), arrows: arrowsOf(d)};
"""


@pytest.mark.parametrize("direction", ["LR", "TD"])
def test_a_loop_back_runs_in_a_lane_beside_the_flow_and_enters_its_target_on_the_lane_side(tmp_path, direction):
    axis = 1 if direction == "LR" else 0
    flows = [f"flowchart {direction}\n  {CHAIN}", f"flowchart {direction}\n  {CHAIN}\n  c --> a"]
    js = """
    return [0, 1].map((o) => {
      const d = drawing(0, o);
      return {steps: stepsOf(d), loops: loopsOf(d), kinds: kindsOf(d), arrows: arrowsOf(d)};
    });
    """
    plain, looped = _run(tmp_path, _round(flows), js)
    steps = _by_id(looped["steps"])
    assert len(looped["loops"]) == 1 and len(looped["arrows"]) == 2
    loop = looped["loops"][0]
    assert loop["index"] == 2 and looped["kinds"] == {"0": "arrow", "1": "arrow", "2": "loop"}
    edge = "y1" if direction == "LR" else "x1"
    beyond = max(s["b"][edge] for s in steps.values())
    assert not any(_crosses(loop["paths"][0], s["b"]) for s in steps.values())
    assert _lane(loop, axis) > beyond + 8
    tip, backs = _head(loop)
    target = steps["a"]["b"]
    assert abs(tip[axis] - target[edge]) <= TOL
    across = 0 if direction == "LR" else 1
    assert target[("x0", "y0")[across]] < tip[across] < target[("x1", "y1")[across]]
    assert all(back[axis] > tip[axis] + 3 for back in backs)
    for mine, theirs in zip(plain["steps"], looped["steps"]):
        assert abs(mine["cx"] - theirs["cx"]) <= TOL and abs(mine["cy"] - theirs["cy"]) <= TOL
    along = "cx" if direction == "LR" else "cy"
    assert looped["steps"][2][along] > looped["steps"][0][along] + 100


@pytest.mark.parametrize("direction", ["LR", "TD"])
def test_the_flows_view_box_grows_by_a_lane_for_each_loop_back(tmp_path, direction):
    flows = [
        f"flowchart {direction}\n  {CHAIN}",
        f"flowchart {direction}\n  {CHAIN}\n  c --> a",
        f"flowchart {direction}\n  {CHAIN}\n  c --> a\n  c --> b",
    ]
    js = """
    return [0, 1, 2].map((o) => {
      const d = drawing(0, o);
      const box = inkOf(d).viewBox.baseVal;
      return {box: {width: box.width, height: box.height}, loops: loopsOf(d)};
    });
    """
    none, one, two = _run(tmp_path, _round(flows), js)
    axis = 1 if direction == "LR" else 0
    size = "height" if direction == "LR" else "width"
    assert none["box"][size] + 10 < one["box"][size] and one["box"][size] + 10 < two["box"][size]
    assert none["box"]["width" if direction == "LR" else "height"] == pytest.approx(
        two["box"]["width" if direction == "LR" else "height"], abs=0.01
    )
    for loop in two["loops"]:
        assert _lane(loop, axis) < two["box"][size]


@pytest.mark.parametrize("direction", ["LR", "TD"])
def test_two_loop_backs_take_two_lanes_the_first_written_nearest_the_flow(tmp_path, direction):
    first = f"flowchart {direction}\n  {CHAIN}\n  c --> a\n  c --> b"
    swapped = f"flowchart {direction}\n  {CHAIN}\n  c --> b\n  c --> a"
    axis = 1 if direction == "LR" else 0
    js = "return [0, 1].map((o) => ({loops: loopsOf(drawing(0, o)), steps: stepsOf(drawing(0, o))}));"
    one, two = _run(tmp_path, _round([first, swapped]), js)
    for got, target_of_first in ((one, "a"), (two, "b")):
        near, far = sorted(got["loops"], key=lambda loop: loop["index"])
        assert near["index"] == 2 and far["index"] == 3
        assert _lane(far, axis) > _lane(near, axis) + 10
        steps = _by_id(got["steps"])
        edge = "y1" if direction == "LR" else "x1"
        assert abs(_head(near)[0][axis] - steps[target_of_first]["b"][edge]) <= TOL


@pytest.mark.parametrize("direction", ["LR", "TD"])
def test_a_step_that_points_at_itself_loops_out_of_and_back_into_it_outside_the_flow(tmp_path, direction):
    axis = 1 if direction == "LR" else 0
    across = 1 - axis
    flow = f"flowchart {direction}\n  {CHAIN}\n  b --> b"
    got = _run(tmp_path, _round([flow]), LOOP_PROBE % 0)
    assert len(got["loops"]) == 1
    loop = got["loops"][0]
    b = _by_id(got["steps"])["b"]["b"]
    edge = ("y1", "x1")[1 - axis]
    start, end = loop["paths"][0][0], _head(loop)[0]
    assert abs(start[axis] - b[edge]) <= TOL and abs(end[axis] - b[edge]) <= TOL
    assert abs(start[across] - end[across]) > 8
    assert _lane(loop, axis) > b[edge] + 8
    assert all(p[axis] >= b[edge] - 2 for p in loop["paths"][0])


def test_written_order_decides_which_arrow_of_a_pair_is_the_loop_back(tmp_path):
    flows = ["flowchart LR\n  a --> b\n  b --> a", "flowchart LR\n  b --> a\n  a --> b"]
    js = """
    return [0, 1].map((o) => {
      const d = drawing(0, o);
      return {loops: loopsOf(d), steps: stepsOf(d), kinds: kindsOf(d)};
    });
    """
    ab, ba = _run(tmp_path, _round(flows), js)
    assert ab["kinds"] == {"0": "arrow", "1": "loop"} and ba["kinds"] == {"0": "arrow", "1": "loop"}
    for got, target in ((ab, "a"), (ba, "b")):
        steps = _by_id(got["steps"])
        assert abs(_head(got["loops"][0])[0][0] - steps[target]["cx"]) <= steps[target]["b"]["x1"] - steps[target]["cx"]
        assert abs(_head(got["loops"][0])[0][1] - steps[target]["b"]["y1"]) <= TOL
    assert _by_id(ab["steps"])["a"]["cx"] < _by_id(ab["steps"])["b"]["cx"]
    assert _by_id(ba["steps"])["b"]["cx"] < _by_id(ba["steps"])["a"]["cx"]


@pytest.mark.parametrize("direction", ["LR", "TD"])
def test_a_loop_back_leaving_a_step_with_a_neighbour_beyond_it_crosses_no_step(tmp_path, direction):
    flow = f"flowchart {direction}\n  a[A] --> b[B]\n  a --> c[C]\n  b --> a\n  c --> a"
    got = _run(tmp_path, _round([flow]), LOOP_PROBE % 0)
    assert sorted(loop["index"] for loop in got["loops"]) == [2, 3]
    steps = _by_id(got["steps"])
    for loop in got["loops"]:
        for step in steps.values():
            assert not _crosses(loop["paths"][0], step["b"]), (loop["index"], step["id"])
        tip, _ = _head(loop)
        edge = steps["a"]["b"]["y1" if direction == "LR" else "x1"]
        assert abs(tip[1 if direction == "LR" else 0] - edge) <= TOL


SHARED_EDGE = """flowchart TD
  a[Plan] --> b[Build] --> c{Tests pass?}
  c -->|no| b
  c -->|yes| d([Ship])
  d -.->|bug found| a
  b --> b"""
HEAD_WIDTH = 2 * 11 * math.sin(math.radians(26))


def test_loop_backs_ending_on_one_steps_lane_side_edge_are_spread_along_it(tmp_path):
    got = _run(tmp_path, _round([SHARED_EDGE]), LOOP_PROBE % 0)
    b = _by_id(got["steps"])["b"]["b"]
    loops = {loop["index"]: loop for loop in got["loops"]}
    assert sorted(loops) == [2, 4, 5]
    ends = [
        ("no tip", _head(loops[2])[0][1]),
        ("self out", loops[5]["paths"][0][0][1]),
        ("self tip", _head(loops[5])[0][1]),
    ]
    for _, y in ends:
        assert b["y0"] <= y <= b["y1"]
    for (name, y), (other, z) in itertools.combinations(ends, 2):
        assert abs(y - z) >= HEAD_WIDTH, (name, other)


TEN_LINES = """flowchart LR
  start([Start]) --> read[Read the order]
  read --> ok{Is it complete?}
  ok -->|yes| route[Route to a team]
  ok -->|no| ask[Ask the customer]
  ask --> fix[Fix the order]
  fix --> read
  route --> notify[Notify the team]
  notify --> done([Done])
  fix -.->|later| done"""


def test_a_decision_whose_no_branch_loops_to_an_earlier_step_draws_with_no_loop_back_crossing_a_step(tmp_path):
    got = _run(tmp_path, _round([TEN_LINES]), LOOP_PROBE % 0)
    assert len(TEN_LINES.split("\n")) == 10
    assert [loop["index"] for loop in got["loops"]] == [5]
    steps = _by_id(got["steps"])
    for step in steps.values():
        assert not _crosses(got["loops"][0]["paths"][0], step["b"]), step["id"]
    tip, _ = _head(got["loops"][0])
    assert abs(tip[1] - steps["read"]["b"]["y1"]) <= TOL
    assert steps["read"]["b"]["x0"] < tip[0] < steps["read"]["b"]["x1"]


def test_dashed_and_labelled_loop_backs_follow_the_forward_arrow_rules(tmp_path):
    flows = [
        f"flowchart LR\n  {CHAIN}\n  c -. try again .-> a",
        f"flowchart LR\n  {CHAIN}\n  c -->|try again| a",
        f"flowchart LR\n  {CHAIN}\n  c --> a",
    ]
    js = """
    return [0, 1, 2].map((o) => {
      const d = drawing(0, o);
      return {loops: loopsOf(d), steps: stepsOf(d)};
    });
    """
    dashed, plain_label, plain = _run(tmp_path, _round(flows), js)
    assert [d != "none" for d in dashed["loops"][0]["dashes"]] == [True, True, False, False, False, False]
    assert plain_label["loops"][0]["dashes"] == ["none"] * 6
    assert plain["loops"][0]["label"] is None and plain["loops"][0]["halo"] is None
    for got in (dashed, plain_label):
        loop = got["loops"][0]
        assert loop["label"]["text"] == "try again" and loop["halo"]["under"]
        below = max(s["b"]["y1"] for s in got["steps"])
        assert loop["label"]["y"] > below + 4
        lane = _lane(loop, 1)
        assert abs(loop["label"]["y"] - lane) <= 4


def _loop_heavy_flow(kind):
    rng = random.Random(11)
    steps = [f"s{i}[{_label(kind, 985, rng)}]" for i in range(60)]
    pairs = [(i, i + 1) for i in range(59)]
    for k in range(61):
        low = k % 58
        pairs.append((low + 1 + k % (59 - low), low))
    arrows = [f"s{a} -->|{_label(kind, 970, rng)}| s{b}" for a, b in pairs]
    return _flow(steps + arrows)


def _ladder_flow():
    rungs = []
    for i in range(58, -1, -1):
        rungs.append(f"s{i} --> s{i + 1}")
        if i < 58:
            rungs.append(f"s{i} --> s{i + 2}")
    return _flow(rungs + ["s59 --> s0", "s30 --> s2", "s58 --> s58"])


BUDGET_PROBE = """
const question = {options: [{flow: %s}]};
const t0 = performance.now();
await PadPen.drawQuestion(document.body, question, 0);
const ms = performance.now() - t0;
const d = drawing(0, 0);
return {ms, steps: stepsOf(d).length, arrows: arrowsOf(d).length, loops: loopsOf(d).length,
        lanes: new Set(loopsOf(d).map((l) => Math.round(Math.max(...l.paths[0].map((p) => p[1]))))).size,
        message: messageOf(d)};
"""


@pytest.mark.parametrize("kind", ["token", "words"])
def test_the_largest_legal_flow_of_mostly_loop_backs_draws_in_under_a_second(tmp_path, kind):
    flow = _loop_heavy_flow(kind)
    assert max(len(line) for line in flow.split("\n")) <= 1000
    got = _run(tmp_path, _round([flow]), BUDGET_PROBE % json.dumps(flow), timeout=120)
    assert got["message"] is None
    assert (got["steps"], got["arrows"], got["loops"]) == (60, 59, 61)
    assert got["lanes"] == 61
    assert got["ms"] < 1000, got["ms"]


def test_a_flow_of_many_forward_routes_finds_its_loop_backs_without_trying_every_route(tmp_path):
    flow = _ladder_flow()
    got = _run(tmp_path, _round([flow]), BUDGET_PROBE % json.dumps(flow), timeout=120)
    assert got["message"] is None
    assert (got["steps"], got["arrows"], got["loops"]) == (60, 117, 3)
    assert got["ms"] < 1000, got["ms"]


def _label(kind, size, rng):
    if kind == "token":
        return "".join(rng.choice("abcdefghijklmnopqrstuvwxyzWMiI") for _ in range(size))
    words = []
    total = 0
    while total < size:
        word = "".join(rng.choice("abcdefghijklmnopqrstuvwxyzWM") for _ in range(rng.randint(2, 9)))
        words.append(word)
        total += len(word) + 1
    return " ".join(words)[:size].rstrip()


def _largest_flow(kind):
    rng = random.Random(7)
    steps = [f"s{i}[{_label(kind, 985, rng)}]" for i in range(60)]
    pairs = [(i, i + 1) for i in range(59)] + [(0, 59)] * 34 + [(1, 3)] * 27
    arrows = [f"s{a} -->|{_label(kind, 970, rng)}| s{b}" for a, b in pairs]
    return _flow(steps + arrows)


@pytest.mark.parametrize("kind", ["token", "words"])
def test_the_largest_legal_flow_draws_in_under_a_second(tmp_path, kind):
    flow = _largest_flow(kind)
    assert max(len(line) for line in flow.split("\n")) <= 1000
    js = """
    const question = {options: [{flow: %s}]};
    const t0 = performance.now();
    await PadPen.drawQuestion(document.body, question, 0);
    const ms = performance.now() - t0;
    const d = drawing(0, 0);
    return {ms, steps: stepsOf(d).length, arrows: arrowsOf(d).length, message: messageOf(d)};
    """ % json.dumps(flow)
    got = _run(tmp_path, _round([flow]), js, timeout=120)
    assert got["message"] is None
    assert (got["steps"], got["arrows"]) == (60, 120)
    assert got["ms"] < 1000, got["ms"]


WRAP = """
return [0, 1, 2].map((o) => {
  const steps = stepsOf(drawing(0, o));
  return steps.map((s) => ({texts: s.texts, b: s.b}));
});
"""
TEN = "check the order and send it to the right team"


def test_labels_wrap_at_about_ten_words_and_a_long_token_breaks_within_its_step(tmp_path):
    flows = [f"flowchart LR\n  a[{TEN}]", f"flowchart LR\n  a[{TEN} {TEN}]", f"flowchart LR\n  a[{'W' * 990}]"]
    ten, twenty, token = _run(tmp_path, _round(flows), WRAP)
    assert len(ten[0]["texts"]) == 1
    assert len(twenty[0]["texts"]) >= 2
    assert len(token[0]["texts"]) >= 3
    for step in (ten[0], twenty[0], token[0]):
        for t in step["texts"]:
            assert t["x1"] - t["x0"] <= 400.5, t
            assert step["b"]["x0"] <= t["x0"] and t["x1"] <= step["b"]["x1"], t


@pytest.mark.parametrize("payload", ["<script>window.pwned=1</script>", "<img src=x onerror=window.pwned=1>"])
def test_markup_in_an_arrow_label_shows_as_text(tmp_path, payload):
    flow = f"flowchart LR\n  a -->|{payload}| b"
    js = """
    return {
      labels: arrowDetail(drawing(0, 0)).map((a) => a.label && a.label.text),
      pwned: typeof window.pwned,
      injected: document.querySelectorAll('#pad script, #pad img').length,
    };
    """
    got = _run(tmp_path, _round([flow]), js)
    assert got["labels"] == [payload]
    assert got["pwned"] == "undefined" and got["injected"] == 0


SERVED_ROUND = {
    "title": "Served flow",
    "questions": [{"prompt": "Q", "options": [_option(0, "flowchart LR\n  a[Served one] --> b{Served two}")]}],
}

SERVED_PROBE = r"""
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
for (let i = 0; i < 200 && !document.querySelector('svg.pad-ink g[data-kind="decision"]'); i++) await sleep(50);
await document.fonts.ready;
const d = document.querySelector('.pad-drawing');
return {
  url: location.href,
  kinds: [...d.querySelectorAll('svg.pad-ink g[data-kind]')].map((g) => g.getAttribute('data-kind')),
  texts: [...d.querySelectorAll('svg.pad-ink text')].map((t) => t.textContent),
  widths: [...d.querySelectorAll('svg.pad-ink text')].map((t) => t.getBBox().width),
  message: d.querySelector('.pen-message') ? d.querySelector('.pen-message').textContent : null,
};
"""


def test_a_flow_on_a_pad_served_under_the_sites_csp_draws_its_steps(tmp_path):
    root = tmp_path / "camp-state"
    member = root / "grp" / "worktrees" / "ws" / "repo"
    member.mkdir(parents=True)
    env = {
        "CAMP_STATE_DIR": str(root),
        "CAMP_CONFIG_DIR": str(tmp_path / "camp-config"),
        "HOME": str(tmp_path / "home"),
        "OUTPOST_STATE_DIR": str(tmp_path / "outpost-state"),
        "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
    }
    round_file = tmp_path / "round.json"
    round_file.write_text(json.dumps(SERVED_ROUND))
    assert scratch_pad.main(["--pad", "p", "--round", str(round_file)], env=env, cwd=member) == 0
    sites = root.resolve() / "grp" / "worktrees" / "ws" / "sites"

    got = ch.run_probe(
        sites,
        SERVED_PROBE,
        serve=True,
        entry="p/",
        inject_into="p/round-1/index.html",
        virtual_time_budget=8000,
    )

    assert got["url"].endswith("/p/round-1/index.html")
    assert got["message"] is None
    assert got["kinds"] == ["box", "decision", "arrow"]
    assert got["texts"] == ["Served one", "Served two"]
    assert all(w > 20 for w in got["widths"])


SCHEMES = ["light", "dark"]

PAINT = r"""
const rgb = (s) => (s.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
const seen = () => ({
  dark: matchMedia('(prefers-color-scheme: dark)').matches,
  bg: getComputedStyle(document.body).backgroundColor,
  ink: getComputedStyle(document.body).color,
});
const strokes = (root) => [...root.querySelectorAll('path')].map((p) => getComputedStyle(p).stroke);
"""

LAYOUT = {"layout": {"column": [{"button": "Save"}, {"box": [{"text": "Hello"}]}]}}


def _paint(tmp_path, scheme, data, js):
    page = _build(tmp_path, data)
    return ch.run_probe(page, LIB + PAINT + "await ready();\n" + js, color_scheme=scheme)


def _lum(color):
    r, g, b = (int(v) / 255 for v in color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _channels(css):
    import re

    return [float(v) for v in re.findall(r"[\d.]+", css)[:3]]


@pytest.mark.parametrize("scheme", SCHEMES)
def test_every_stroke_is_in_the_pages_ink_and_the_page_is_dark_paper_only_when_dark_is_preferred(tmp_path, scheme):
    got = _paint(
        tmp_path,
        scheme,
        _round([LAYOUT, "flowchart LR\n  a[One] -.-> b[Two]\n  b --> a"]),
        "return {...seen(), layout: strokes(drawing(0, 0)), flow: strokes(drawing(0, 1))};",
    )
    assert got["dark"] == (scheme == "dark")
    assert got["layout"] and got["flow"]
    assert set(got["layout"]) == {got["ink"]}
    assert set(got["flow"]) == {got["ink"]}
    bg, ink = _lum(_channels(got["bg"])), _lum(_channels(got["ink"]))
    assert (bg < 0.2 and ink > 0.7) if scheme == "dark" else (bg > 0.8 and ink < 0.2)


ACCENT_FLOW = "flowchart LR\n  a[One]:::accent --> b[Two]\n  b --> c[Three]"


@pytest.mark.parametrize("scheme", SCHEMES)
def test_an_accented_steps_strokes_are_the_accent_colour_and_its_arrows_and_other_steps_stay_ink(tmp_path, scheme):
    got = _paint(
        tmp_path,
        scheme,
        _round([ACCENT_FLOW]),
        """
        const d = drawing(0, 0);
        const group = (id) => d.querySelector(`g[data-step-id="${id}"]`);
        return {
          ...seen(),
          accented: strokes(group('a')),
          hatch: [...group('a').querySelectorAll('.flow-hatch')].map((l) => getComputedStyle(l).stroke),
          plain: strokes(group('b')),
          arrows: strokes(d.querySelector('g[data-kind="arrow"]')),
        };
        """,
    )
    assert got["dark"] == (scheme == "dark")
    accent = got["accented"][0]
    assert accent != got["ink"]
    assert set(got["accented"]) == {accent}
    assert got["hatch"] and set(got["hatch"]) == {accent}
    assert set(got["plain"]) == {got["ink"]}
    assert set(got["arrows"]) == {got["ink"]}


def test_the_accent_colour_differs_between_light_and_dark(tmp_path):
    js = "return getComputedStyle(drawing(0, 0).querySelector('g[data-step-id=\"a\"] path')).stroke;"
    data = _round([ACCENT_FLOW])
    assert _paint(tmp_path, "light", data, js) != _paint(tmp_path, "dark", data, js)


def _inside(poly, point, slack):
    """Whether point is in the traced polygon (even-odd) or within slack of its edge."""
    px, py = point
    crossings = 0
    for (x1, y1), (x2, y2) in zip(poly, poly[1:]):
        if (y1 > py) != (y2 > py) and px < x1 + (py - y1) * (x2 - x1) / (y2 - y1):
            crossings += 1
        dx, dy = x2 - x1, y2 - y1
        t = 0 if dx == dy == 0 else max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
        if math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)) <= slack:
            return True
    return crossings % 2 == 1


SHAPES = {"box": "a[One]", "round": "a(One)", "pill": "a([One])", "decision": "a{One}"}


@pytest.mark.parametrize("shape", SHAPES)
def test_every_hatch_line_of_an_accented_step_is_diagonal_and_ends_inside_its_outline(tmp_path, shape):
    flow = f"flowchart LR\n  {SHAPES[shape]}:::accent --> b[Two]"
    got = _run(
        tmp_path,
        _round([flow]),
        """
        const num = (l, n) => Number(l.getAttribute(n));
        const group = (id) => drawing(0, 0).querySelector(`g[data-step-id="${id}"]`);
        return {
          outline: pathsOf(group('a'))[0],
          hatch: [...group('a').querySelectorAll('.flow-hatch')]
            .map((l) => [num(l, 'x1'), num(l, 'y1'), num(l, 'x2'), num(l, 'y2')]),
          plain: group('b').querySelectorAll('.flow-hatch').length,
        };
        """,
    )
    assert len(got["hatch"]) >= 3
    assert got["plain"] == 0
    for x1, y1, x2, y2 in got["hatch"]:
        assert abs(abs(x2 - x1) - abs(y2 - y1)) < 0.5
        assert _inside(got["outline"], (x1, y1), 2.0) and _inside(got["outline"], (x2, y2), 2.0)


def test_hatching_does_not_move_any_outline_or_arrow_stroke(tmp_path):
    marked = "flowchart LR\n  a[One]:::accent --> b[Two]\n  b --> c[Three]"
    assert _run(tmp_path, _round([marked]), "return everyPath(drawing(0, 0));") == _plain_paths(tmp_path)


@pytest.mark.parametrize("scheme", SCHEMES)
def test_cards_pressed_buttons_note_and_reply_boxes_and_the_font_notice_contrast_with_their_text(tmp_path, scheme):
    got = _paint(
        tmp_path,
        scheme,
        _round([LAYOUT, "flowchart LR\n  a[One] -->|go| b[Two]"]),
        """
        const pressed = document.querySelector('.pad-toggle');
        pressed.setAttribute('aria-pressed', 'true');
        const current = document.querySelector('.pad-questions button[aria-current="true"]');
        const of = (el) => ({bg: getComputedStyle(el).backgroundColor, fg: getComputedStyle(el).color});
        const notice = document.getElementById('pad-font-notice');
        return {
          ...seen(),
          card: of(document.querySelector('.pad-card')),
          pressed: of(pressed),
          current: of(current),
          note: of(document.querySelector('.pad-note')),
          reply: of(document.querySelector('.pad-reply-box')),
          notice: of(notice),
          halo: getComputedStyle(drawing(0, 1).querySelector('.flow-halo')).fill,
        };
        """,
    )
    assert got["dark"] == (scheme == "dark")
    for name in ("card", "pressed", "current", "note", "reply", "notice"):
        bg, fg = _lum(_channels(got[name]["bg"])), _lum(_channels(got[name]["fg"]))
        assert abs(bg - fg) > 0.5, f"{name}: background {got[name]['bg']} on text {got[name]['fg']}"
    assert got["halo"] == got["bg"]
    page_bg = _lum(_channels(got["bg"]))
    assert (page_bg < 0.2) if scheme == "dark" else (page_bg > 0.8)
    for name in ("card", "current", "note", "reply"):
        assert (_lum(_channels(got[name]["bg"])) < 0.3) == (scheme == "dark")


def test_two_accented_drawings_on_one_question_share_no_svg_id(tmp_path):
    got = _run(
        tmp_path,
        _round([ACCENT_FLOW, "flowchart TD\n  x{Pick}:::accent --> y([End]):::accent"]),
        """
        const ids = [...document.querySelectorAll('svg.pad-ink [id]')].map((e) => e.id);
        const optionOf = (el) => el.closest('.pad-drawing').dataset.option;
        const refs = [...document.querySelectorAll('svg.pad-ink *')].flatMap((e) =>
          [...e.attributes].filter((a) => /url\\(#/.test(a.value)).map((a) => [optionOf(e), a.value]));
        const owner = (id) => (document.getElementById(id) ? optionOf(document.getElementById(id)) : null);
        return {ids, dupes: ids.length - new Set(ids).size,
          refs: refs.map(([option, value]) => [option, owner(value.match(/#([^)]+)/)[1])]),
          hatch: [...document.querySelectorAll('.flow-hatch')].length};
        """,
    )
    assert got["hatch"] > 0
    assert got["dupes"] == 0
    assert all(own == mine for mine, own in got["refs"])
