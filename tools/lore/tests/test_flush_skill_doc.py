"""Doc-consistency checks for the session-finalization skill.

The `finish` skill was renamed to `flush` and rewritten for the clean/dirty +
candidate-evaluation model. These tests track the `flush` skill's structural
invariants: `finish` must be gone, `flush` must be present, and its frontmatter
must stay registrable.
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
    """'finish' was renamed to 'flush'. Guard the rename is stable."""
    assert not (SKILLS_DIR / "finish" / "SKILL.md").exists(), (
        "finish/SKILL.md must not exist — it was renamed to flush"
    )
    assert FLUSH_SKILL.exists(), "flush/SKILL.md must exist after the rename"


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
