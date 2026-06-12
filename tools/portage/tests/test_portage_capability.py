"""B-4 analog: portage's capabilities.toml resolves to real on-disk skills/agents.

Mirrors craft's test_release_capability.py contract for the new portage plugin:

  - capabilities.toml exists and parses (stdlib tomllib — the craft test harness
    cannot import trailhead.capabilities, same constraint here).
  - [tool] name == "portage" and matches the plugins/portage/ dir name.
  - Every skill listed in any [capabilities.*] group resolves to an existing
    SKILL.md under the plugin root.
  - Every agent listed in any [capabilities.*] group resolves to an existing
    .md file under the plugin root.
  - validate semantics: every listed path must exist (the load-bearing risk is
    the relative-path-from-plugin-root resolution).

Hermeticity: pure path-existence checks; no network.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES_TOML = REPO_ROOT / "capabilities.toml"
PLUGIN_ROOT = REPO_ROOT / "plugins" / "portage"


def _load_caps() -> dict:
    with open(CAPABILITIES_TOML, "rb") as f:
        return tomllib.load(f)


def test_capabilities_toml_exists():
    assert CAPABILITIES_TOML.exists(), (
        f"capabilities.toml not found at {CAPABILITIES_TOML}"
    )


def test_tool_name_is_portage_and_matches_dir():
    caps = _load_caps()
    assert caps.get("tool", {}).get("name") == "portage", (
        "[tool] name must be 'portage'"
    )
    assert PLUGIN_ROOT.is_dir(), (
        f"[tool] name='portage' must match the plugins/portage/ dir at {PLUGIN_ROOT}"
    )


def test_has_at_least_one_capability_group():
    caps = _load_caps()
    groups = caps.get("capabilities", {})
    assert groups, "capabilities.toml must declare at least one [capabilities.*] group"


def _all_skills(caps: dict) -> list[str]:
    out: list[str] = []
    for group in caps.get("capabilities", {}).values():
        out.extend(group.get("skills", []))
    return out


def _all_agents(caps: dict) -> list[str]:
    out: list[str] = []
    for group in caps.get("capabilities", {}).values():
        out.extend(group.get("agents", []))
    return out


def test_all_capability_skills_resolve_to_existing_skill_md():
    caps = _load_caps()
    skills = _all_skills(caps)
    assert skills, "portage must declare at least one skill across its capabilities"
    for skill_rel in skills:
        skill_md = PLUGIN_ROOT / skill_rel / "SKILL.md"
        assert skill_md.exists(), (
            f"capability skill {skill_rel!r} resolves to {skill_md} which does NOT "
            "exist (dangling reference)"
        )


def test_all_capability_agents_resolve_to_existing_files():
    caps = _load_caps()
    agents = _all_agents(caps)
    assert agents, "portage must declare at least one agent across its capabilities"
    for agent_rel in agents:
        resolved = PLUGIN_ROOT / agent_rel
        assert resolved.exists(), (
            f"capability agent {agent_rel!r} resolves to {resolved} which does NOT "
            "exist (dangling reference)"
        )


def test_expected_portage_skills_declared():
    """All four portage skills (open/merge/update/monitor) are in a capability group."""
    caps = _load_caps()
    skills = set(_all_skills(caps))
    for name in ("open", "merge", "update", "monitor"):
        assert f"skills/{name}" in skills, (
            f"portage capability must declare skills/{name} — declared: {sorted(skills)}"
        )


def test_expected_portage_agents_declared():
    """All three portage agents (summarizer/updater/monitor) are in a capability group."""
    caps = _load_caps()
    agents = set(_all_agents(caps))
    for name in ("summarizer", "updater", "monitor"):
        assert f"agents/{name}.md" in agents, (
            f"portage capability must declare agents/{name}.md — declared: {sorted(agents)}"
        )
