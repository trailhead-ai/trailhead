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
    """Guard against a skill dir silently disappearing."""
    names = {p.parent.name for p in _skill_files()}
    expected = {
        "defer", "dead-end", "decision", "radar", "subsystem",
        "checkpoint", "finished", "vault-sync", "ping",
        "reflect", "check-radar",
    }
    missing = expected - names
    assert not missing, f"expected skills missing from the plugin: {sorted(missing)}"
