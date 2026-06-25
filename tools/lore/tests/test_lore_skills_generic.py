"""Every shipped lore skill and template must be generic — zero brain-vault
structural strings and zero private app-specific tokens.

This test enforces the mechanical definition of "generic" via structural brain
seams, parametrized over skills discovered in plugins/lore/skills/*/SKILL.md,
and additionally scans plugins/lore/templates/*.md so templates cannot smuggle
a private token past the per-skill scan.

## Structural brain seams (literal strings — never denylisted)
These strings definitionally belong to brain's private infrastructure. They are
safe to embed as literals here because they do NOT appear in the machine-local
leak-gate.denylist (the denylist carries identifying tokens; "mcp__brain__" and
"code/brain" are structural — deliberately kept off the denylist so THIS file
can reference them without tripping the gate).

  - "mcp__brain__"  — brain MCP tool prefix
  - "code/brain"    — matches ~/code/brain and /Users/.../code/brain

Identifying tokens (developer handle / org name / machine path) are NOT checked
here — those are the leak gate's exclusive responsibility. Adding them here as
literals would trip the gate on this file itself (the P1-F self-referential trap).

`skills/_shared/` is a reference doc, not a skill, and is exempt.

## Template hygiene
templates/*.md are shipped public files. They receive the same structural-seam
check as SKILL.md files so a token cannot bypass the per-skill scan by living
in a template. App-flavored tokens are constructed at runtime below (same
P1-F trap avoidance as the test_cli_new.py band).
"""

from __future__ import annotations

from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).parent.parent / "plugins" / "lore" / "skills"
TEMPLATES_DIR = Path(__file__).parent.parent / "plugins" / "lore" / "templates"

STRUCTURAL_SEAMS: list[str] = [
    "mcp__brain__",
    "code/brain",
]

# Private app-specific tokens constructed at runtime to avoid the P1-F
# self-referential leak-gate trap (the test file is itself scanned by the gate).
_PRIVATE_TOKENS: list[str] = [
    "".join(["post", "hog"]),
    "".join(["dash", "0"]),
    "".join(["evidence", "_", "pack"]),
    "".join(["ze", "nith", "health"]),
    "".join(["as", "ana"]),
    "".join(["plat", "form", "."]),
    "".join(["mobile", "-app"]),
]


def _skill_files() -> list[Path]:
    return sorted(
        d / "SKILL.md"
        for d in SKILLS_DIR.iterdir()
        if d.is_dir() and d.name != "_shared" and (d / "SKILL.md").exists()
    )


def _template_files() -> list[Path]:
    if not TEMPLATES_DIR.exists():
        return []
    return sorted(p for p in TEMPLATES_DIR.glob("*.md") if p.name != ".gitkeep")


@pytest.mark.parametrize("skill_md", _skill_files(), ids=lambda p: p.parent.name)
def test_skill_has_no_structural_brain_seams(skill_md: Path):
    """Skill must contain no structural brain-vault strings."""
    text = skill_md.read_text()
    for seam in STRUCTURAL_SEAMS:
        assert seam not in text, (
            f"{skill_md.parent.name}/SKILL.md contains the structural brain seam "
            f"{seam!r}. Genericize: drop mcp__brain__ tools, strip code/brain paths."
        )


@pytest.mark.parametrize("template_md", _template_files(), ids=lambda p: p.stem)
def test_template_has_no_structural_brain_seams(template_md: Path):
    """Template must contain no structural brain-vault strings."""
    text = template_md.read_text()
    for seam in STRUCTURAL_SEAMS:
        assert seam not in text, (
            f"templates/{template_md.name} contains the structural brain seam "
            f"{seam!r}. Templates are shipped public files — remove brain-private strings."
        )


@pytest.mark.parametrize("template_md", _template_files(), ids=lambda p: p.stem)
def test_template_has_no_private_tokens(template_md: Path):
    """Template must contain no private app-specific tokens."""
    text = template_md.read_text().lower()
    for token in _PRIVATE_TOKENS:
        assert token.lower() not in text, (
            f"templates/{template_md.name} contains the private token {token!r}. "
            "Templates are shipped public files — use generic, provider-agnostic prose."
        )


# ---------------------------------------------------------------------------
# brainstorm — moved to the craft plugin (S6 Slice 3).
#
# The brainstorm skill and its generic-hygiene / visible-skip assertions moved to
# craft (tools/craft/tests/test_brainstorm_generic.py). The structural-brain-seam
# and private-token scans above still cover whatever lore skills remain.
# ---------------------------------------------------------------------------


