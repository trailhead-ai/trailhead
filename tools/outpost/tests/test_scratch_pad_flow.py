"""Behavioural tests for the scratch-pad kit's flow drawing.

Every test builds a real pad with ``scratch_pad.build_pad`` from the real kit, opens the built
round folder in a headless browser through the probe harness, and asserts on the strokes the
pen traced and the text the browser measured and set.
"""

from __future__ import annotations

import importlib.util
import json
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
    .filter((g) => g.getAttribute('data-kind') !== 'arrow')
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
        "  d --> p([Pill with a rather long label that must wrap onto lines])\n"
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
    assert len(got["message"]) == 3 and got["message"][2]
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


@pytest.mark.parametrize(
    ("flow", "line", "reason"),
    [
        ("flowchart LR\n  a --> b\n  subgraph x", "line 3: subgraph x", "subgraph"),
        ("a --> b", "line 1: a --> b", "flowchart"),
        ("\n\nflowchart RL\n  a --> b", "line 3: flowchart RL", "flowchart"),
        ("flowchart LR\n  a --> b\n  b --> a", "line 3: b --> a", "loops are not drawn yet"),
        ("flowchart LR\n  a --> a", "line 2: a --> a", "loops are not drawn yet"),
        ("flowchart LR\n  a --> b\n  b --> c\n  c --> a", "line 4: c --> a", "loops are not drawn yet"),
        ("flowchart LR\n  a((round)) --> b", "line 2: a((round)) --> b", "step"),
    ],
    ids=["subgraph", "no-header", "bad-direction", "loop", "self-loop", "long-loop", "other-shape"],
)
def test_a_flow_that_cannot_be_drawn_shows_three_paragraphs_naming_the_line(tmp_path, flow, line, reason):
    got = _run(tmp_path, _round([flow, SIBLING]), MESSAGE)
    assert len(got["message"]) == 3
    assert got["message"][0] == "Can't draw this flow:"
    assert got["message"][1] == line
    assert reason in got["message"][2]
    assert got["svgs"] == 0
    assert got["sibling"] == ["box", "button"]


@pytest.mark.parametrize(
    "flow", ["", "flowchart LR", "\n  \n flowchart TD \n\n"], ids=["empty", "header-alone", "blank-lines"]
)
def test_an_empty_flow_shows_the_whole_flow_message(tmp_path, flow):
    got = _run(tmp_path, _round([flow, SIBLING]), MESSAGE)
    assert got["message"] == ["Can't draw this flow:", "the whole flow", "The flow has no steps."]
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
