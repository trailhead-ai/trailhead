"""Dev-layer consolidation structural tests.

This test verifies:
  1. Root /.claude-plugin/marketplace.json exists, parses, name == "trailhead-local",
     plugins[] has exactly 5 entries, every source starts with ./tools/ and resolves
     to an existing plugins/<tool>/.claude-plugin/plugin.json under the repo root.
  2. No tools/*/.claude-plugin/marketplace.json remains.
  3. Repo-wide grep guard: no trailhead-{lore,camp,craft,portage,landing} marketplace
     names and no @trailhead-<tool> / <tool>-local install refs remain in source/docs
     (excluding bin/migrate-marketplaces.sh and trailhead/tests/).

Write BEFORE implementation — these tests must fail RED first, then pass GREEN after.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_ROOT_MARKETPLACE = _REPO_ROOT / ".claude-plugin" / "marketplace.json"
_TOOLS = ["lore", "camp", "craft", "portage", "landing"]


# ---------------------------------------------------------------------------
# T-D1: Root marketplace.json exists and has the right shape
# ---------------------------------------------------------------------------


class TestRootMarketplaceShape:
    def test_root_marketplace_exists(self):
        assert _ROOT_MARKETPLACE.exists(), (
            f"Root marketplace not found: {_ROOT_MARKETPLACE}\n"
            "Expected /.claude-plugin/marketplace.json at repo root"
        )

    def test_root_marketplace_parses(self):
        data = json.loads(_ROOT_MARKETPLACE.read_text())
        assert isinstance(data, dict), "marketplace.json must be a JSON object"

    def test_root_marketplace_name_is_trailhead_local(self):
        data = json.loads(_ROOT_MARKETPLACE.read_text())
        assert data.get("name") == "trailhead-local", (
            f"Expected name='trailhead-local', got: {data.get('name')!r}"
        )

    def test_root_marketplace_has_five_plugins(self):
        data = json.loads(_ROOT_MARKETPLACE.read_text())
        plugins = data.get("plugins", [])
        assert len(plugins) == 5, (
            f"Expected 5 plugin entries, got {len(plugins)}: {[p.get('name') for p in plugins]}"
        )

    def test_root_marketplace_plugin_names(self):
        data = json.loads(_ROOT_MARKETPLACE.read_text())
        names = {p.get("name") for p in data.get("plugins", [])}
        assert names == set(_TOOLS), f"Expected plugin names {set(_TOOLS)}, got {names}"

    def test_every_source_starts_with_tools(self):
        data = json.loads(_ROOT_MARKETPLACE.read_text())
        for entry in data.get("plugins", []):
            src = entry.get("source", "")
            assert src.startswith("./tools/"), (
                f"Plugin {entry.get('name')!r}: source must start with './tools/', got {src!r}"
            )

    def test_every_source_resolves_to_plugin_json(self):
        data = json.loads(_ROOT_MARKETPLACE.read_text())
        for entry in data.get("plugins", []):
            tool = entry.get("name")
            src = entry.get("source", "")
            # source is relative to repo root; resolve the plugin.json path
            plugin_json = _REPO_ROOT / src / ".claude-plugin" / "plugin.json"
            assert plugin_json.exists(), (
                f"Plugin {tool!r}: source={src!r} does not resolve to an existing "
                f"plugins/{tool}/.claude-plugin/plugin.json.\n"
                f"Expected: {plugin_json}"
            )
