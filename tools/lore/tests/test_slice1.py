"""Manifest-validity tests (originally Slice 1, repointed in Slice 4).

The Slice-1 smoke hook (session_smoke.py / _shared_smoke.py) was removed once
the real lifecycle hooks landed in Slice 4. These tests preserve the durable
coverage that survived that removal: plugin.json / marketplace.json / hooks.json
remain valid and structurally correct.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"


# ---------------------------------------------------------------------------
# Manifest validity — plugin.json, marketplace.json, hooks/hooks.json
# ---------------------------------------------------------------------------

def test_plugin_json_parses_and_has_required_keys():
    """plugin.json is valid JSON and has name, version, description."""
    path = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    assert path.exists(), f"Expected {path} to exist"
    data = json.loads(path.read_text())
    assert "name" in data, "plugin.json must have 'name'"
    assert "version" in data, "plugin.json must have 'version'"
    assert "description" in data, "plugin.json must have 'description'"
    assert data["name"] == "lore"


def test_marketplace_json_parses_and_has_required_keys():
    """marketplace.json is valid JSON and has name, owner, plugins with required fields."""
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


def test_hooks_json_parses_and_registers_session_start():
    """hooks/hooks.json is valid JSON with a hooks.SessionStart entry whose
    command references the plugin via ${CLAUDE_PLUGIN_ROOT}."""
    path = PLUGIN_ROOT / "hooks" / "hooks.json"
    assert path.exists(), f"Expected {path} to exist"
    data = json.loads(path.read_text())
    assert "hooks" in data, "hooks.json must have top-level 'hooks' key"
    hooks = data["hooks"]
    assert "SessionStart" in hooks, "hooks.json must register SessionStart"
    entries = hooks["SessionStart"]
    assert isinstance(entries, list) and len(entries) >= 1
    for entry in entries:
        assert "hooks" in entry
        for h in entry["hooks"]:
            assert "type" in h
            assert "command" in h
            assert "${CLAUDE_PLUGIN_ROOT}" in h["command"]
