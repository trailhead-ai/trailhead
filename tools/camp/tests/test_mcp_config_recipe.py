"""Recipe-shape regression test for the mcp-config provision task.

`.mcp.json` is gitignored, so it is never part of a git worktree checkout — a
camp workspace built without this task never gets the MCP server config the
repo's own agents are told to use. This task copies the repo root's
`.mcp.json` into the new worktree as a provision-phase step, mirroring the
code-review-graph recipe test's shape-pinning approach.

The step copies the existing file rather than regenerating it: `code-review-graph
install` emits a `uvx`-based command that never completes the MCP stdio
handshake, while the repo-root `.mcp.json` already points at the resolved
binary.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_GROUPS_EXAMPLE_DIR = _REPO_ROOT / "tools" / "camp" / "groups.example"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

_TASK_NAME = "mcp-config"


def _resolved_recipe() -> dict:
    """The trailhead member's resolved mcp-config task from the shipped
    example config."""
    from camp.group.config import load_group

    cfg = load_group(_GROUPS_EXAMPLE_DIR / "trailhead.toml")
    member = next(m for m in cfg["members"] if m["name"] == "trailhead")
    return next(t for t in member["tasks"] if t["name"] == _TASK_NAME)


def test_example_config_recipe_is_single_copy_step() -> None:
    task = _resolved_recipe()

    assert task["phase"] == "provision"
    assert task["required"] is False
    assert task["steps"] == [
        {
            "name": "copy",
            "cmd": ["cp", "{repo_root}/.mcp.json", "{worktree}/.mcp.json"],
        }
    ]


def test_trailhead_member_references_mcp_config_task() -> None:
    from camp.group.config import load_group

    cfg = load_group(_GROUPS_EXAMPLE_DIR / "trailhead.toml")
    member = next(m for m in cfg["members"] if m["name"] == "trailhead")

    assert any(t["name"] == _TASK_NAME for t in member["tasks"])
