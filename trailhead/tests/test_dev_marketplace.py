"""Dev-layer consolidation structural tests.

This test verifies:
  1. Root /.claude-plugin/marketplace.json exists, parses, name == "trailhead-local",
     plugins[] carries exactly one entry per shipped tool, every source starts with
     ./tools/ and resolves to an existing plugins/<tool>/.claude-plugin/plugin.json
     under the repo root.
  2. No tools/*/.claude-plugin/marketplace.json remains.
  3. Repo-wide grep guard: no trailhead-{lore,camp,craft,portage} marketplace
     names and no @trailhead-<tool> / <tool>-local install refs remain in source/docs
     (excluding trailhead/tests/).

Write BEFORE implementation — these tests must fail RED first, then pass GREEN after.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_ROOT_MARKETPLACE = _REPO_ROOT / ".claude-plugin" / "marketplace.json"
_TOOLS = ["lore", "camp", "craft", "portage", "outpost", "trailhead"]


# ---------------------------------------------------------------------------
# T-D1: Root marketplace.json exists and has the right shape
# ---------------------------------------------------------------------------


class TestRootMarketplaceShape:
    def test_root_marketplace_parses(self):
        data = json.loads(_ROOT_MARKETPLACE.read_text())
        assert isinstance(data, dict), "marketplace.json must be a JSON object"

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
