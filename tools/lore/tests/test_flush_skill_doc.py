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


# ---------------------------------------------------------------------------
# `lore flush` closes with the full sync flow — no separate mandatory step
# ---------------------------------------------------------------------------

def test_flush_skill_documents_the_builtin_sync_tail():
    """The sync is part of `lore flush` now; `--no-sync` is the documented opt-out."""
    text = _skill_text()
    assert "sync tail" in text.lower(), (
        "flush/SKILL.md must document the built-in sync tail `lore flush` ends with"
    )
    assert "--no-sync" in text, (
        "flush/SKILL.md must document `--no-sync` as the opt-out from the tail"
    )


def test_flush_skill_no_longer_mandates_a_separate_sync_step():
    """The old 'Step 5 is not optional — call `lore sync`' instruction is dead."""
    text = _skill_text()
    lowered = text.lower()
    assert "is not optional" not in lowered, (
        "flush/SKILL.md must not still mandate a separate `lore sync` step"
    )
    assert "call `lore sync`" not in lowered, (
        "flush/SKILL.md must not instruct a follow-up `lore sync` — the tail does it"
    )


def test_flush_skill_does_not_claim_flush_commits_nothing_else():
    """True of the session commit, false at command scope: every 'nothing else'
    claim must say which of the two it is talking about."""
    text = _skill_text()
    for line in text.splitlines():
        if "nothing else" in line:
            assert "session" in line.lower() or "--no-sync" in line, (
                "flush/SKILL.md claims flush commits 'nothing else' without "
                f"scoping the claim to the session commit or `--no-sync`: {line!r}"
            )
    assert "are NOT committed by `lore flush`" not in text, (
        "flush/SKILL.md must not claim the records it created stay uncommitted"
    )


def test_flush_skill_names_lore_resolve_as_the_tail_conflict_remedy():
    """A tail rebase conflict exits 0; the stderr remedy is `lore resolve <vault>`."""
    text = _skill_text()
    assert "lore resolve" in text, (
        "flush/SKILL.md must name `lore resolve` as the remedy when the sync tail "
        "hits a rebase conflict"
    )
