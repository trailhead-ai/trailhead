"""The `/camp:bookmark` skill ships as a discoverable, CLI-delegating skill.

A skill becomes selectable purely by convention — `skills/<name>/SKILL.md` under
the plugin root — so the guard here is that the file exists in the right place,
carries the frontmatter the harness needs to route the slash command, and points
the agent at the `camp` CLI rather than restating what the CLI does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trailhead.capabilities import load_manifest

_REPO_ROOT = Path(__file__).parent.parent.parent
_SKILL = (
    _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "skills" / "bookmark" / "SKILL.md"
)


@pytest.fixture()
def body() -> str:
    return _SKILL.read_text()


def test_skill_file_exists_where_discovery_looks():
    assert _SKILL.is_file()


def test_skill_is_selectable_from_the_camp_manifest():
    """Discovery is on-disk, so the installer must offer it without a manifest edit."""
    manifest = load_manifest(_REPO_ROOT / "tools" / "camp" / "capabilities.toml")
    assert manifest.skills.get("bookmark") == "skills/bookmark"


def test_frontmatter_declares_name_and_description(body: str):
    assert body.startswith("---\n")
    frontmatter = body.split("---\n", 2)[1]
    assert "name: bookmark\n" in frontmatter
    assert "description:" in frontmatter


def test_skill_delegates_to_the_camp_cli(body: str):
    """Thin wrapper: the skill runs the CLI; it never reimplements the store."""
    for command in ("camp bookmark", "camp bookmark ls", "camp bookmark rm", "camp resume"):
        assert command in body
    assert "bookmarks.json" not in body
