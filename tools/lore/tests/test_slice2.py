"""Slice 2 tests: doc-consistency check for the `finish` skill.

Verifies the structural invariants of the `lore:finish` SKILL.md that are
independent of the (now-retired, S6 Slice 1) harvest-expansion flow:

1. The skill describes `lore:finish` as the canonical end-of-session finish.
2. The skill frontmatter stays registrable.

The harvest-expansion assertions that previously lived here were dropped when
S6 Slice 1 retired the lore-side harvest flow (`lore finish` is now
finalize + commit only). The skill body rewrite is S6 Slice 4.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SKILLS_DIR = REPO_ROOT / "plugins" / "lore" / "skills"
FINISHED_SKILL = SKILLS_DIR / "finish" / "SKILL.md"


def _skill_text() -> str:
    return FINISHED_SKILL.read_text()


# ---------------------------------------------------------------------------
# 1. lore:finish framed as the canonical end-of-session finish
# ---------------------------------------------------------------------------


def test_skill_framed_as_canonical():
    """The skill must describe lore:finish as the canonical end-of-session finish."""
    text = _skill_text()
    assert "canonical" in text.lower(), (
        "finish/SKILL.md should frame lore:finish as the canonical end-of-session finish"
    )


# ---------------------------------------------------------------------------
# 2. Skill frontmatter is still registrable (non-regression)
# ---------------------------------------------------------------------------


def test_finished_skill_frontmatter_still_registrable():
    """Editing the skill must not break the frontmatter that Claude Code needs."""
    text = FINISHED_SKILL.read_text()
    assert text.startswith("---\n"), "finish/SKILL.md must still open with a YAML frontmatter block"
    end = text.find("\n---", 3)
    assert end > 0, "finish/SKILL.md frontmatter block must still be closed"
    frontmatter = text[3:end]
    desc_lines = [
        ln
        for ln in frontmatter.splitlines()
        if ln.strip().startswith("description:") and ln.split(":", 1)[1].strip()
    ]
    assert desc_lines, "finish/SKILL.md must still carry a non-empty description:"
