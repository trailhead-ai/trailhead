"""Every shipped skill must be registrable by Claude Code.

A SKILL.md only registers as an invocable `/lore:<name>` command if it opens
with a YAML frontmatter block carrying at least a `description:`. Six capture
skills originally shipped without frontmatter and silently never registered —
this test locks the invariant so that can't regress.

`skills/_shared/` is a reference doc, not a skill, and is exempt.
"""
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).parent.parent / "plugins" / "lore" / "skills"


def _skill_files() -> list[Path]:
    return sorted(
        d / "SKILL.md"
        for d in SKILLS_DIR.iterdir()
        if d.is_dir() and d.name != "_shared" and (d / "SKILL.md").exists()
    )


@pytest.mark.parametrize("skill_md", _skill_files(), ids=lambda p: p.parent.name)
def test_skill_has_registrable_frontmatter(skill_md: Path):
    text = skill_md.read_text()
    assert text.startswith("---\n"), (
        f"{skill_md.parent.name}/SKILL.md must open with a `---` frontmatter "
        "block or Claude Code will not register it as a /lore: command"
    )
    end = text.find("\n---", 3)
    assert end > 0, f"{skill_md.parent.name}/SKILL.md frontmatter block is not closed"
    frontmatter = text[3:end]
    desc_lines = [
        ln for ln in frontmatter.splitlines()
        if ln.strip().startswith("description:") and ln.split(":", 1)[1].strip()
    ]
    assert desc_lines, (
        f"{skill_md.parent.name}/SKILL.md frontmatter must carry a non-empty "
        "`description:` (it's what drives skill triggering)"
    )


def test_all_capture_and_ritual_skills_present():
    """Guard against a skill dir silently disappearing.

    Note: 'subsystem' was renamed to 'area'; 'vault-sync' was renamed to 'sync';
    'finished' was renamed to 'finish'; the watchlist skill was renamed to
    'follow-up'; its polling companion was renamed to 'check-in'. Slice 7
    DELETED the 'reflect', 'tend'/'review', and 'ping' skills entirely.
    S6 Slice 2 DELETED the 7 obsolete per-kind capture skills: 'area',
    'check-in', 'dead-end', 'decision', 'defer', 'follow-up', 'seed'.
    These are replaced by the `lore record` / `lore session` CLI surface.

    S6 Slice 3 MOVED 'brainstorm' to the craft plugin. S6 Slice 5 ADDED the
    three new skills 'search' (read path / replaces recall), 'record' (single
    deliberate capture), and 'research' (dispatch investigator/researcher), so
    the retained lore skills are: checkpoint, finish, sync, search, record,
    research (+ _shared, exempt).
    """
    names = {p.parent.name for p in _skill_files()}
    expected = {
        "checkpoint", "finish", "sync",
        "search", "record", "research",
    }
    missing = expected - names
    assert not missing, f"expected skills missing from the plugin: {sorted(missing)}"


def test_brainstorm_moved_to_craft_absent_from_lore():
    """S6 Slice 3 moved the brainstorm skill out of lore into the craft plugin.

    Guard against it being accidentally re-added under lore — its home is now
    tools/craft/plugins/craft/skills/brainstorm/.
    """
    names = {p.parent.name for p in _skill_files()}
    assert "brainstorm" not in names, (
        "brainstorm must not exist under the lore plugin — S6 Slice 3 moved it to "
        "the craft plugin (tools/craft/plugins/craft/skills/brainstorm/)"
    )


def test_obsolete_per_kind_capture_skills_absent():
    """S6 Slice 2 deleted the 7 obsolete per-kind capture skills.

    These skills are replaced by the `lore record` / `lore session` CLI surface.
    Guard against them being accidentally re-added.
    """
    names = {p.parent.name for p in _skill_files()}
    deleted = {"area", "check-in", "dead-end", "decision", "defer", "follow-up", "seed"}
    present = deleted & names
    assert not present, (
        f"obsolete per-kind capture skills must not exist (S6 Slice 2 deleted them): "
        f"{sorted(present)}"
    )
