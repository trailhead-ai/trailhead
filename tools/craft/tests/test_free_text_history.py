"""The seeded conversation the three free-text-answer eval cases resume.

A real session that loads a skill gets the skill's text in its context, right after the
launch line: a "Base directory for this skill" line, the body without its front matter, with
the plugin root filled in, and the arguments. The seeded conversation reproduces that launch
against the copy of the plugin the cases' scaffold installs in the run directory, so the
resumed session answers with the procedure in front of it, as a real one would.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parents[1] / "plugins" / "craft" / "evals"
BUILDER = EVALS / "slice-carries-out-a-free-text-answer-to-its-question" / "fixtures" / "build_history.py"
SKILL = Path(__file__).resolve().parents[1] / "plugins" / "craft" / "skills" / "slice" / "SKILL.md"
CASES = [
    "slice-carries-out-a-free-text-answer-to-its-question",
    "slice-asks-again-on-a-free-text-answer-that-cannot-be-undone",
    "slice-amends-the-spec-on-a-free-text-answer-that-changes-it",
]


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location("build_history", BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("body", "args", "expected_body"),
    [
        ("# Slice\n\nRun `${CLAUDE_PLUGIN_ROOT}/scripts/a.py`.\n", "spec/one",
         "# Slice\n\nRun `./craft/scripts/a.py`.\n"),
        ("# Other\n\nNo root here.\n", "spec/two", "# Other\n\nNo root here.\n"),
    ],
)
def test_launch_text_is_the_body_a_real_launch_shows(builder, tmp_path, body, args, expected_body):
    skill = tmp_path / "slice" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("---\nname: slice\ndescription: >\n  x\n---\n" + body, encoding="utf-8")

    text = builder.launch_text(skill, args)

    assert text == (
        "Base directory for this skill: ./craft/skills/slice\n\n" + expected_body + f"\n\nARGUMENTS: {args}"
    )


def test_build_puts_the_launch_text_right_after_the_skill_launch(builder, monkeypatch):
    monkeypatch.setattr(builder, "captured_show", lambda: "revision: r\n# Invoice summary\n")

    events = [json.loads(line) for line in builder.build().splitlines()]

    launch = next(i for i, e in enumerate(events) if _tool_result_text(e) == "Launching skill: craft:slice")
    skill_use = next(u for u in (_tool_use(e, "Skill") for e in events) if u)
    meta = events[launch + 1]
    assert meta["isMeta"] is True
    assert meta["sourceToolUseID"] == skill_use["id"]
    assert meta["parentUuid"] == events[launch]["uuid"]
    assert meta["message"]["content"][0]["text"] == builder.launch_text(SKILL, "spec/invoice-summary")


@pytest.mark.parametrize("case", CASES)
def test_committed_history_carries_the_current_skill_text(builder, case):
    events = [json.loads(line) for line in (EVALS / case / "fixtures" / "history.jsonl").read_text().splitlines()]

    metas = [e for e in events if e.get("isMeta")]

    assert [m["message"]["content"][0]["text"] for m in metas] == [
        builder.launch_text(SKILL, "spec/invoice-summary")
    ], "history.jsonl is stale: run fixtures/build_history.py in the carries-out case"


def _tool_use(event: dict, name: str) -> dict | None:
    content = event.get("message", {}).get("content")
    if event.get("type") != "assistant" or not isinstance(content, list):
        return None
    return next((c for c in content if c.get("type") == "tool_use" and c.get("name") == name), None)


def _tool_result_text(event: dict) -> str | None:
    content = event.get("message", {}).get("content")
    if event.get("type") != "user" or not isinstance(content, list):
        return None
    return next((c.get("content") for c in content if c.get("type") == "tool_result"), None)
