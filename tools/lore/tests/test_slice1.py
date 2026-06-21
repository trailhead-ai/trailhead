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


# NOTE: the per-tool .claude-plugin/marketplace.json was removed when the dev
# marketplace consolidated into the repo-root `trailhead-local` marketplace.
# Its shape + source-resolution guard now live in
# trailhead/tests/test_dev_marketplace.py at the monorepo level.


def test_hooks_json_parses_and_has_no_push_hooks():
    """hooks/hooks.json is valid JSON with zero push hooks.

    Slice 2, S5 (F5): SessionStart and WorktreeRemove entries were removed —
    lore is fully pull; orientation lives in agent-rules and S6 skill descriptions.
    Slice 1 (lore-agent-interface): PostToolUse harvest-candidates entry removed —
    lore installs zero push hooks.
    """
    path = PLUGIN_ROOT / "hooks" / "hooks.json"
    assert path.exists(), f"Expected {path} to exist"
    data = json.loads(path.read_text())
    assert "hooks" in data, "hooks.json must have top-level 'hooks' key"
    hooks = data["hooks"]
    assert "SessionStart" not in hooks, "hooks.json must NOT register SessionStart (F5)"
    assert "WorktreeRemove" not in hooks, "hooks.json must NOT register WorktreeRemove (Slice 2)"
    assert "PostToolUse" not in hooks, (
        "hooks.json must NOT register PostToolUse — harvest hook deleted (lore-agent-interface Slice 1)"
    )
    assert hooks == {}, (
        f"hooks.json must have empty hooks dict — lore installs zero push hooks, got: {list(hooks.keys())}"
    )
