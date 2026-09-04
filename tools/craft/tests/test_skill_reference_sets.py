"""A skill that reads `_shared/execute.md` end to end must name every
`_shared` document that procedure's rules depend on, in the SKILL.md itself.

`execute.md` states three of its rules — task-status ownership, the
standalone refine procedure, and design-doc state coverage — at the point of
use without naming `status-ownership.md`, `refine.md`, or `slice.md` by path
(see `reference_depth_gate.py`). A reader who opens only the SKILL.md must
still reach all three in one hop: they are promoted into the reference set of
every SKILL.md that reads `execute.md` end to end, so no rule the agent is
held to sits behind a reference a partial read can drop.

This asserts structurally that each filename is named — not that it is read
in any particular order, or with any particular wording around it.
"""

from __future__ import annotations

from pathlib import Path

SKILLS = Path(__file__).parent.parent / "plugins" / "craft" / "skills"

# The three sibling `_shared` documents `execute.md`'s rules draw on without
# naming any of them by path.
EXECUTE_MD_DEPENDENCIES = ("status-ownership.md", "refine.md", "slice.md")


def _text(skill_name: str) -> str:
    return (SKILLS / skill_name / "SKILL.md").read_text(encoding="utf-8")


def test_execute_skill_names_every_shared_document_execute_md_needs():
    text = _text("execute")
    missing = [name for name in EXECUTE_MD_DEPENDENCIES if name not in text]
    assert not missing, f"skills/execute/SKILL.md never names {missing}"


def test_drive_skill_names_every_shared_document_execute_md_needs():
    """`drive/SKILL.md` reads `_shared/execute.md` end to end at its build
    phase, so it needs the same reference set `execute/SKILL.md` needs."""
    text = _text("drive")
    missing = [name for name in EXECUTE_MD_DEPENDENCIES if name not in text]
    assert not missing, f"skills/drive/SKILL.md never names {missing}"
