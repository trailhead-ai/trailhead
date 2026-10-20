"""The scratch-pad skill's own round example must work when a session copies it.

The example is taken from the first fenced ``json`` block of SKILL.md that parses as a round. It is
run through ``check_round``, built into a pad with ``build_pad`` and opened in a headless browser,
where every option, flows included, must be drawn rather than replaced by a "Can't draw" message.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

import chromium_harness as ch

SKILL_DIR = Path(__file__).parent.parent / "plugins" / "outpost" / "skills" / "scratch-pad"

_spec = importlib.util.spec_from_file_location("scratch_pad", SKILL_DIR / "scratch_pad.py")
scratch_pad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scratch_pad)

PROBE = r"""
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const out = [];
const questions = window.PAD_ROUND.questions.length;
for (let q = 0; q < questions; q++) {
  document.querySelectorAll('nav button')[q].click();
  for (let waited = 0; waited < 40000; waited += 50) {
    const all = [...document.querySelectorAll(`.pad-drawing[data-question="${q}"]`)];
    if (all.length && all.every((d) => d.querySelector('svg.pad-ink path, .pen-message'))) break;
    await sleep(50);
  }
  for (const d of document.querySelectorAll(`.pad-drawing[data-question="${q}"]`)) {
    out.push({
      question: q,
      option: Number(d.getAttribute('data-option')),
      strokes: d.querySelectorAll('svg.pad-ink path').length,
      message: d.querySelector('.pen-message') ? d.querySelector('.pen-message').textContent : null,
    });
  }
}
return out;
"""


def skill_example_round(text: str | None = None) -> dict:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8") if text is None else text
    for block in re.findall(r"```json\n(.*?)```", text, re.DOTALL):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and "questions" in data:
            return data
    raise AssertionError("SKILL.md has no round example")


def test_the_skill_example_is_a_round_the_script_accepts():
    assert scratch_pad.check_round(skill_example_round()) == []


def test_the_skill_example_draws_every_option_and_includes_a_flow(tmp_path, monkeypatch):
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    data = skill_example_round()
    round_path = tmp_path / "round.json"
    round_path.write_text(json.dumps(data))
    _, pad = scratch_pad.build_pad(round_path, "demo", env={"OUTPOST_STATE_DIR": str(tmp_path / "state")})

    drawn = ch.run_probe(pad / "round-1", PROBE)

    options = [(q, o) for q, question in enumerate(data["questions"]) for o in range(len(question["options"]))]
    assert [(d["question"], d["option"]) for d in drawn] == options
    assert [d["message"] for d in drawn if d["message"]] == []
    assert all(d["strokes"] > 0 for d in drawn)
    flows = [(q, o) for q, o in options if "flow" in data["questions"][q]["options"][o]]
    assert flows, "the example shows no flow option"


@pytest.mark.parametrize("flow", ["flowchart LR\nsubgraph g\nA --> B\nend", "flowchart LR\nA ==> B"])
def test_a_broken_flow_in_the_example_is_caught_by_the_probe(tmp_path, monkeypatch, flow):
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    data = {
        "title": "t",
        "questions": [{"prompt": "p", "options": [{"name": "n", "cost": "c", "pick": True, "flow": flow}]}],
    }
    round_path = tmp_path / "round.json"
    round_path.write_text(json.dumps(data))
    _, pad = scratch_pad.build_pad(round_path, "demo", env={"OUTPOST_STATE_DIR": str(tmp_path / "state")})

    drawn = ch.run_probe(pad / "round-1", PROBE)

    assert "Can't draw this flow" in drawn[0]["message"]
