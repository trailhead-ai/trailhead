"""Slice 2 tests: doc-consistency check for the session-finalization skill.

S6 Slice 2 originally verified the `finish` skill's structural invariants.
Plan Slice 4 renamed `finish` → `flush` and rewrote the skill for the
clean/dirty + candidate-evaluation model. These tests now track the `flush`
skill's non-regression invariants that the Slice 4 rewrite must not break.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SKILLS_DIR = REPO_ROOT / "plugins" / "lore" / "skills"
FLUSH_SKILL = SKILLS_DIR / "flush" / "SKILL.md"


def _skill_text() -> str:
    return FLUSH_SKILL.read_text()


# ---------------------------------------------------------------------------
# 1. finish skill is gone; flush skill is present
# ---------------------------------------------------------------------------

def test_finish_skill_absent_flush_skill_present():
    """Plan Slice 4 renamed 'finish' to 'flush'. Guard the rename is stable."""
    assert not (SKILLS_DIR / "finish" / "SKILL.md").exists(), (
        "finish/SKILL.md must not exist — Slice 4 renamed it to flush"
    )
    assert FLUSH_SKILL.exists(), "flush/SKILL.md must exist after the Slice 4 rename"


# ---------------------------------------------------------------------------
# 2. Skill frontmatter is still registrable (non-regression)
# ---------------------------------------------------------------------------

def test_flush_skill_frontmatter_still_registrable():
    """Editing the skill must not break the frontmatter that Claude Code needs."""
    text = _skill_text()
    assert text.startswith("---\n"), "flush/SKILL.md must still open with a YAML frontmatter block"
    end = text.find("\n---", 3)
    assert end > 0, "flush/SKILL.md frontmatter block must still be closed"
    frontmatter = text[3:end]
    desc_lines = [
        ln for ln in frontmatter.splitlines()
        if ln.strip().startswith("description:") and ln.split(":", 1)[1].strip()
    ]
    assert desc_lines, "flush/SKILL.md must still carry a non-empty description:"
