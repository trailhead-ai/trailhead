"""Contract tests for the wired ``tools/outpost`` plugin's structural anatomy.

``tools/outpost`` is the fifth trailhead plugin: skill-only (no python package, no
agents), modelled on ``tools/portage``'s anatomy. Its skills are discovered on
disk and, being a pure convention-based inventory, must load cleanly and expose
``publish-site`` as one of the discoverable skills.

These tests pin the structural anatomy of the plugin so a future edit can't
silently break the wiring.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_TOOL_ROOT = _REPO_ROOT / "tools" / "outpost"
_CAPABILITIES = _TOOL_ROOT / "capabilities.toml"
_PLUGIN_JSON = _TOOL_ROOT / "plugins" / "outpost" / ".claude-plugin" / "plugin.json"


# ---------------------------------------------------------------------------
# Anatomy: skill-only plugin modelled on portage (no python package, no agents)
# ---------------------------------------------------------------------------


class TestPluginAnatomy:
    def test_capabilities_toml_exists(self):
        assert _CAPABILITIES.exists(), f"missing {_CAPABILITIES}"

    def test_capabilities_is_skill_only(self):
        """No always-on `base`, no hooks — the skill is discovered on disk."""
        from trailhead.capabilities import load_manifest

        m = load_manifest(_CAPABILITIES)
        assert m.base == [], "skill-only plugin must declare no `base` set"
        assert m.hooks_json is None, "skill-only plugin declares no hooks_json"

    def test_plugin_json_exists_and_names_outpost(self):
        import json

        assert _PLUGIN_JSON.exists(), f"missing {_PLUGIN_JSON}"
        data = json.loads(_PLUGIN_JSON.read_text())
        assert data.get("name") == "outpost"
        assert data.get("description", "").strip() != ""

    def test_no_python_package_or_agents(self):
        """Skill-only: no <name>/ package, no agents/ dir under the plugin root."""
        plugin_root = _TOOL_ROOT / "plugins" / "outpost"
        assert not (plugin_root / "outpost").exists(), "skill-only: no python package"
        assert not (plugin_root / "agents").exists(), "skill-only: no subagents"

    def test_no_per_tool_marketplace_json(self):
        """The single-marketplace convention: no per-tool marketplace.json remains."""
        assert not (_TOOL_ROOT / ".claude-plugin" / "marketplace.json").exists(), (
            "trailhead uses a single root marketplace; tools/outpost must not carry "
            "its own .claude-plugin/marketplace.json"
        )

    def test_plugin_loads_and_publish_site_is_discoverable(self):
        """The manifest loads cleanly and publish-site is a discovered skill."""
        from trailhead.capabilities import load_manifest

        m = load_manifest(_CAPABILITIES)
        assert m.skills.get("publish-site") == "skills/publish-site"


# ---------------------------------------------------------------------------
# No zenith-era naming survives anywhere under tools/outpost
# ---------------------------------------------------------------------------


def test_no_zenith_tools_reference_anywhere():
    """The rewritten plugin carries no `zenith-tools` (or `zenith`) naming."""
    offenders: list[str] = []
    for path in _TOOL_ROOT.rglob("*"):
        if path.is_file() and "zenith" in path.read_text(errors="ignore").lower():
            offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert not offenders, f"zenith-era naming survives in: {offenders}"
