"""portage's plugin surface is discoverable by convention.

The capability-GROUP model was removed — install now selects subagents/skills by
name from the inventory discovered on disk (skills/<name>/SKILL.md + agents/*.md).
This test pins the new contract for portage:

  - capabilities.toml exists, parses, and [tool] name == "portage" (matches dir).
  - it declares NO [capabilities.*] groups (the schema dropped them).
  - the expected skills (open/merge/update/monitor) exist as skills/<name>/SKILL.md.
  - the expected agents (summarizer/updater/monitor) exist as agents/<name>.md.

Hermeticity: pure path-existence checks; no network. (Plugin-local test — uses
stdlib tomllib, not trailhead.capabilities.)
"""
from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES_TOML = REPO_ROOT / "capabilities.toml"
PLUGIN_ROOT = REPO_ROOT / "plugins" / "portage"


def _load_caps() -> dict:
    with open(CAPABILITIES_TOML, "rb") as f:
        return tomllib.load(f)


def _on_disk_skills() -> set[str]:
    skills = PLUGIN_ROOT / "skills"
    return {
        d.name for d in skills.iterdir()
        if d.is_dir() and (d / "SKILL.md").is_file()
    } if skills.is_dir() else set()


def _on_disk_agents() -> set[str]:
    agents = PLUGIN_ROOT / "agents"
    return {
        p.stem for p in agents.glob("*.md") if p.is_file()
    } if agents.is_dir() else set()


def test_capabilities_toml_exists():
    assert CAPABILITIES_TOML.exists(), f"capabilities.toml not found at {CAPABILITIES_TOML}"


def test_tool_name_is_portage_and_matches_dir():
    caps = _load_caps()
    assert caps.get("tool", {}).get("name") == "portage", "[tool] name must be 'portage'"
    assert PLUGIN_ROOT.is_dir(), f"plugins/portage/ dir missing at {PLUGIN_ROOT}"


def test_no_capability_groups():
    caps = _load_caps()
    assert "capabilities" not in caps, (
        "capability groups were removed — install selects subagents/skills by name"
    )


def test_expected_portage_skills_present():
    skills = _on_disk_skills()
    for name in ("open", "merge", "update", "monitor"):
        assert name in skills, f"portage skill {name!r} missing — found: {sorted(skills)}"


def test_expected_portage_agents_present():
    agents = _on_disk_agents()
    for name in ("summarizer", "updater", "monitor"):
        assert name in agents, f"portage agent {name!r} missing — found: {sorted(agents)}"
