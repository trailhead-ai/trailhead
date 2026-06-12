"""Every shipped landing skill must be registrable by Claude Code.

A SKILL.md only registers as an invocable `/landing:<name>` command if it opens
with a YAML frontmatter block carrying a non-empty `name:` and `description:`.
This test locks the invariant so a skill can't silently fail to register, and
pins `name:` == the skill dir name.

Unique basename so it does not collide with craft's/lore's/portage's same-named test.
"""
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).resolve().parents[1] / "plugins" / "landing" / "skills"


def _skill_files() -> list[Path]:
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(
        d / "SKILL.md"
        for d in SKILLS_DIR.iterdir()
        if d.is_dir() and d.name != "_shared" and (d / "SKILL.md").exists()
    )


def test_both_landing_skills_present():
    names = {p.parent.name for p in _skill_files()}
    expected = {"soak", "resolve"}
    missing = expected - names
    assert not missing, f"expected landing skills missing: {sorted(missing)}"


def _frontmatter(skill_md: Path) -> str:
    text = skill_md.read_text()
    assert text.startswith("---\n"), (
        f"{skill_md.parent.name}/SKILL.md must open with a `---` frontmatter block "
        "or Claude Code will not register it as a /landing: command"
    )
    end = text.find("\n---", 3)
    assert end > 0, f"{skill_md.parent.name}/SKILL.md frontmatter block is not closed"
    return text[3:end]


def _field(frontmatter: str, field: str) -> str | None:
    for ln in frontmatter.splitlines():
        if ln.strip().startswith(f"{field}:"):
            return ln.split(":", 1)[1].strip()
    return None


@pytest.mark.parametrize("skill_md", _skill_files(), ids=lambda p: p.parent.name)
def test_skill_has_registrable_frontmatter(skill_md: Path):
    fm = _frontmatter(skill_md)
    assert _field(fm, "name"), (
        f"{skill_md.parent.name}/SKILL.md frontmatter must carry a non-empty `name:`"
    )
    assert _field(fm, "description"), (
        f"{skill_md.parent.name}/SKILL.md frontmatter must carry a non-empty `description:`"
    )


@pytest.mark.parametrize("skill_md", _skill_files(), ids=lambda p: p.parent.name)
def test_skill_name_matches_dir(skill_md: Path):
    fm = _frontmatter(skill_md)
    name = _field(fm, "name")
    assert name == skill_md.parent.name, (
        f"{skill_md.parent.name}/SKILL.md frontmatter name={name!r} must equal the "
        f"skill dir name {skill_md.parent.name!r} (registers as /landing:{skill_md.parent.name})"
    )
