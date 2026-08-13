"""Every shipped lore agent must be generic — zero brain-vault structural strings.

This test enforces the mechanical definition of "generic" via structural brain
seams, parametrized over agents discovered in plugins/lore/agents/*.md —
mirroring the Band-1 structural-seam pattern test_lore_skills_generic.py already
runs over skills/*/SKILL.md and templates/*.md, and the sibling craft
implementation over plugins/craft/agents/*.md (test_agents_generic.py).

## Structural brain seams (literal strings — never denylisted)
These strings definitionally belong to brain's private infrastructure. They are
safe to embed as literals here because they do NOT appear in the machine-local
leak-gate.denylist (the denylist carries identifying tokens; "mcp__brain__" and
"code/brain" are structural — deliberately kept off the denylist so THIS file
can reference them without tripping the gate).

  - "mcp__brain__"  — brain MCP tool prefix
  - "code/brain"    — matches ~/code/brain and /Users/.../code/brain

Identifying tokens (developer handle / org name / machine path) are NOT checked
here — those are the leak gate's exclusive responsibility, by the same
established convention restated in test_lore_skills_generic.py,
tools/craft/tests/test_agents_generic.py, and
tools/craft/tests/test_craft_skills_generic.py. Concretely: this test does
**not** assert that an agent is free of a hardcoded personal name — that class
of drift is the machine-local leak-gate's job, not this test's. Only the
structural brain-vault seams above are in scope here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

AGENTS_DIR = Path(__file__).parent.parent / "plugins" / "lore" / "agents"

STRUCTURAL_SEAMS: list[str] = [
    "mcp__brain__",
    "code/brain",
]


def _agent_files() -> list[Path]:
    if not AGENTS_DIR.exists():
        return []
    return sorted(AGENTS_DIR.glob("*.md"))


@pytest.mark.parametrize("agent_md", _agent_files(), ids=lambda p: p.stem)
def test_agent_has_no_structural_brain_seams(agent_md: Path):
    """Agent must contain no structural brain-vault strings."""
    text = agent_md.read_text()
    for seam in STRUCTURAL_SEAMS:
        assert seam not in text, (
            f"{agent_md.name} contains the structural brain seam {seam!r}. "
            "Genericize: drop mcp__brain__ tools, strip code/brain paths."
        )
