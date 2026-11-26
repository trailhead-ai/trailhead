"""Every reason code `checklist_answer.py` can print has a per-code sentence in the `/craft:checks`
skill and in the design doc. The expected set is `checklist_answer.REASON_CODES`, read from the
module, so a code the writer gains or a sentence that stops naming one is caught. A sentence may
cover a group of codes as long as it names each one in backticks."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN = _ROOT / "tools" / "craft" / "plugins" / "craft"
_SKILL = _PLUGIN / "skills" / "checks" / "SKILL.md"
_DESIGN = _ROOT / "docs" / "design" / "answer-pending-checks-on-the-check-list.md"
_SCRIPT = _PLUGIN / "scripts" / "checklist_answer.py"

sys.path.insert(0, str(_SCRIPT.parent))
_spec = importlib.util.spec_from_file_location("checklist_answer_for_doc_consistency", _SCRIPT)
checklist_answer = importlib.util.module_from_spec(_spec)
sys.modules["checklist_answer_for_doc_consistency"] = checklist_answer
_spec.loader.exec_module(checklist_answer)

_BACKTICKED = re.compile(r"`([a-z][a-z0-9-]*)`")


def section(markdown: str, start_heading: str, end_heading: str | None = None) -> str:
    """The text from the heading starting `start_heading` up to the next `## ` heading,
    or to the heading starting `end_heading`."""
    lines = markdown.splitlines()
    begin = next(i for i, ln in enumerate(lines) if ln.startswith(start_heading))
    stop = len(lines)
    for i in range(begin + 1, len(lines)):
        if end_heading is not None and lines[i].startswith(end_heading):
            stop = i
            break
        if end_heading is None and lines[i].startswith("## "):
            stop = i
            break
    return "\n".join(lines[begin:stop])


def named_codes(text: str) -> set[str]:
    return set(_BACKTICKED.findall(text))


def skill_sentences() -> str:
    return section(_SKILL.read_text(encoding="utf-8"), "## Results")


def design_sentences() -> str:
    return section(
        _DESIGN.read_text(encoding="utf-8"),
        "## State — answer refused as stale",
        "## State — walk-through stopped",
    )


def test_every_reason_code_has_a_sentence_in_the_skill():
    missing = set(checklist_answer.REASON_CODES) - named_codes(skill_sentences())
    assert not missing, sorted(missing)


def test_every_reason_code_has_a_sentence_in_the_design_doc():
    missing = set(checklist_answer.REASON_CODES) - named_codes(design_sentences())
    assert not missing, sorted(missing)


@pytest.mark.parametrize("dropped", ["spec-unreadable", "stale", "credential-shaped-words"])
def test_a_code_missing_from_a_section_is_found_missing(dropped):
    text = "## Results\n" + " ".join(f"`{c}`" for c in checklist_answer.REASON_CODES if c != dropped) + "\n## Next\n"
    found_missing = set(checklist_answer.REASON_CODES) - named_codes(section(text, "## Results"))
    assert found_missing == {dropped}


def test_a_code_named_outside_the_section_does_not_count():
    text = "## Intro\n`spec-unreadable`\n## Results\n`stale`\n## Next\n`write-failed`\n"
    assert named_codes(section(text, "## Results")) == {"stale"}
