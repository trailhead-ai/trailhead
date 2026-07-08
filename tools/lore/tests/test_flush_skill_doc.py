"""Doc-consistency checks for the session-finalization skill.

The `flush` skill finalizes a session under the clean/dirty + candidate-evaluation
model. These tests track the `flush` skill's structural invariants: it must be
present and its frontmatter must stay registrable.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SKILLS_DIR = REPO_ROOT / "plugins" / "lore" / "skills"
FLUSH_SKILL = SKILLS_DIR / "flush" / "SKILL.md"


def _skill_text() -> str:
    return FLUSH_SKILL.read_text()


# ---------------------------------------------------------------------------
# Skill frontmatter is registrable
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


# ---------------------------------------------------------------------------
# Ambient-capture ritual: candidate -> `open` task with auto-provenance
# ---------------------------------------------------------------------------

def test_flush_skill_documents_task_as_evaluation_outcome():
    """`backlog`/`plan` are retired; the evaluation-outcome kind list must name
    `task` as the outcome for open work items, not the removed kinds."""
    text = _skill_text()
    assert "`task`" in text, (
        "flush/SKILL.md must document `task` as a candidate-evaluation outcome"
    )
    assert "backlog" not in text, (
        "flush/SKILL.md must not reference the retired `backlog` kind"
    )
    assert "`plan`" not in text, (
        "flush/SKILL.md must not reference the retired `plan` kind"
    )


def test_flush_skill_documents_ambient_capture_auto_provenance():
    """The ambient-capture ritual auto-provenances a promoted `task`: the active
    parent task as `related` (provenance, not membership) and
    `related-files-or-folders` — no extra judgment call needed for these fields."""
    text = _skill_text()
    assert "auto-provenance" in text, (
        "flush/SKILL.md must name the auto-provenance ambient-capture ritual"
    )
    assert "related-files-or-folders" in text or "--related-file" in text, (
        "flush/SKILL.md must document auto-provenancing related-files-or-folders"
    )
    assert "parent" in text.lower(), (
        "flush/SKILL.md must document auto-provenancing the active parent task"
    )
