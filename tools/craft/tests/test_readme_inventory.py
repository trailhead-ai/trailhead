"""README completeness against the on-disk plugin inventory.

Pins tools/craft/README.md's Skills and Agents sections against the real
skills/*/SKILL.md and agents/*.md files, the same convention
trailhead/tests/test_capabilities.py uses for the manifest inventory, so the
README's lists cannot silently drift from what the plugin actually ships.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "craft"
README = REPO_ROOT / "README.md"


def _discovered_skills() -> list[str]:
    return sorted(
        d.name
        for d in (PLUGIN_ROOT / "skills").iterdir()
        if d.is_dir() and d.name != "_shared" and (d / "SKILL.md").exists()
    )


def _discovered_agents() -> list[str]:
    return sorted(p.stem for p in (PLUGIN_ROOT / "agents").glob("*.md"))


def test_every_shipped_skill_is_listed_in_readme():
    readme_text = README.read_text()
    missing = [
        name
        for name in _discovered_skills()
        if f"/craft:{name}" not in readme_text
    ]
    assert not missing, f"README Skills section is missing: {missing}"


def test_every_shipped_agent_is_listed_in_readme():
    readme_text = README.read_text()
    missing = [
        name
        for name in _discovered_agents()
        if f"craft:{name}" not in readme_text
    ]
    assert not missing, f"README Agents section is missing: {missing}"
