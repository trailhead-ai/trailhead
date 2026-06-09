"""Manifest-validity tests for the forge plugin packaging.

Mirrors lore's packaging coverage: the root marketplace.json and the
plugins/forge/plugin.json must be valid JSON with the required fields, and the
marketplace `source` must resolve to the real plugin directory. `source: "."`
is rejected by Claude Code, so the plugin must live in a `plugins/forge/`
subdir referenced by `source: "./plugins/forge"`.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "forge"


def test_plugin_json_parses_and_has_required_keys():
    """plugin.json is valid JSON and has name, version, description."""
    path = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    assert path.exists(), f"Expected {path} to exist"
    data = json.loads(path.read_text())
    assert "name" in data, "plugin.json must have 'name'"
    assert "version" in data, "plugin.json must have 'version'"
    assert "description" in data, "plugin.json must have 'description'"
    assert data["name"] == "forge"


def test_marketplace_json_parses_and_has_required_keys():
    """marketplace.json is valid JSON with name, owner, and plugins entries."""
    path = REPO_ROOT / ".claude-plugin" / "marketplace.json"
    assert path.exists(), f"Expected {path} to exist"
    data = json.loads(path.read_text())
    assert "name" in data, "marketplace.json must have 'name'"
    assert "owner" in data, "marketplace.json must have 'owner'"
    assert "name" in data["owner"], "marketplace.json owner must have 'name'"
    assert "plugins" in data, "marketplace.json must have 'plugins'"
    assert len(data["plugins"]) >= 1
    plugin = data["plugins"][0]
    assert "name" in plugin, "Each plugin entry must have 'name'"
    assert "source" in plugin, "Each plugin entry must have 'source'"


def test_marketplace_source_resolves_to_plugin_dir():
    """The marketplace `source` must point at an existing plugins/<name>/ dir
    that contains a plugin.json. Guards against `source: "."` (rejected by
    Claude Code) regressing back in."""
    path = REPO_ROOT / ".claude-plugin" / "marketplace.json"
    data = json.loads(path.read_text())
    source = data["plugins"][0]["source"]
    assert source != ".", 'source: "." is rejected by Claude Code; use "./plugins/<name>"'
    resolved = (REPO_ROOT / source).resolve()
    assert resolved.is_dir(), f"marketplace source {source!r} does not resolve to a dir"
    assert (resolved / ".claude-plugin" / "plugin.json").exists(), (
        f"plugin source {source!r} has no .claude-plugin/plugin.json"
    )
