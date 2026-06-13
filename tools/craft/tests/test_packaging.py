"""Manifest-validity tests for the craft plugin packaging.

Mirrors lore's packaging coverage: the root marketplace.json and the
plugins/craft/plugin.json must be valid JSON with the required fields, and the
marketplace `source` must resolve to the real plugin directory. `source: "."`
is rejected by Claude Code, so the plugin must live in a `plugins/craft/`
subdir referenced by `source: "./plugins/craft"`.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "craft"


def test_plugin_json_parses_and_has_required_keys():
    """plugin.json is valid JSON and has name, version, description."""
    path = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    assert path.exists(), f"Expected {path} to exist"
    data = json.loads(path.read_text())
    assert "name" in data, "plugin.json must have 'name'"
    assert "version" in data, "plugin.json must have 'version'"
    assert "description" in data, "plugin.json must have 'description'"
    assert data["name"] == "craft"


# NOTE: craft's per-tool .claude-plugin/marketplace.json was removed when the
# dev marketplace consolidated into the repo-root `trailhead-local` marketplace.
# The marketplace shape and the `source: "."` regression guard now live in
# trailhead/tests/test_dev_marketplace.py at the monorepo level.
