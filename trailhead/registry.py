"""Harness-registration concern for trailhead composed plugins.

This module owns the narrow responsibility of registering a composed
trailhead plugin tree with the Claude Code harness marketplace system.

Architecture
------------
``registry.py`` is **not** a planner or writer — it owns only:

1. ``generate_marketplace_json`` — writes the Shape-A marketplace.json
   at ``<mkt_root>/.claude-plugin/marketplace.json``.
2. ``register`` — shells the harness CLI (``claude plugin marketplace add``
   then ``claude plugin install``) to register and install a newly composed
   plugin.
3. ``rewire`` — shells the harness CLI (``claude plugin update``) to refresh
   an already-registered plugin after recomposition.

Hermeticity contract (B-3)
--------------------------
The CLI invocation is injectable via a ``runner=`` keyword argument.
Tests ALWAYS pass a stub runner and NEVER invoke the real ``claude plugin``
CLI against the user's harness.  The default runner is ``subprocess.run``
with ``check=True``; it is only exercised in live-session dogfood runs.

registry.py NEVER writes to ``~/.claude/plugins/`` directly — the harness
CLI manages ``known_marketplaces.json`` and the plugin cache; registry only
generates ``marketplace.json`` under the ``state_dir``-rooted ``mkt_root``.

Shape-A marketplace.json
-------------------------
The generated marketplace.json follows Shape A (validated live via
``claude plugin validate``):

    {
      "name": "trailhead-<tool>",
      "owner": {"name": "trailhead"},
      "description": "...",
      "plugins": [
        {
          "name": "<tool>",
          "source": "./plugins/<tool>",
          "description": "..."
        }
      ]
    }

The ``source: "./plugins/<tool>"`` is relative to ``mkt_root``, which is
where ``wire.py`` composes the plugin tree into
``<mkt_root>/plugins/<tool>/``.
"""

import json
import subprocess
from pathlib import Path


_TOOL_DESCRIPTIONS: dict[str, str] = {
    "lore": (
        "Portable knowledge-management plugin: session lifecycle, "
        "capture skills, and vault recall."
    ),
    "camp": (
        "Portable worktree-orchestration plugin: group config, "
        "dev-env orchestration, and worktree management."
    ),
    "forge": (
        "Portable software-development plugin: general-purpose dev "
        "agents and dev-ritual skills."
    ),
}

_MARKETPLACE_DESCRIPTION = "Trailhead-composed plugin marketplace for {tool}."


def _tool_description(tool: str) -> str:
    return _TOOL_DESCRIPTIONS.get(
        tool,
        f"Trailhead-composed plugin for {tool}.",
    )


def generate_marketplace_json(tool: str, mkt_root: Path) -> None:
    """Write the Shape-A marketplace.json at <mkt_root>/.claude-plugin/.

    Args:
        tool:     Tool name (e.g. "lore", "camp", "forge").
        mkt_root: Marketplace root directory.  ``plugins/<tool>/`` under this
                  root is where compose writes the plugin tree.
    """
    claude_plugin_dir = mkt_root / ".claude-plugin"
    claude_plugin_dir.mkdir(parents=True, exist_ok=True)

    marketplace = {
        "name": f"trailhead-{tool}",
        "owner": {"name": "trailhead"},
        "description": _MARKETPLACE_DESCRIPTION.format(tool=tool),
        "plugins": [
            {
                "name": tool,
                "source": f"./plugins/{tool}",
                "description": _tool_description(tool),
            }
        ],
    }

    out = claude_plugin_dir / "marketplace.json"
    out.write_text(json.dumps(marketplace, indent=2))


def register(
    tool: str,
    mkt_root: Path,
    *,
    runner=None,
) -> None:
    """Register and install a composed plugin via the harness CLI.

    Shells:
      1. ``claude plugin marketplace add --scope user <mkt_root>``
      2. ``claude plugin install <tool>@trailhead-<tool> --scope user``

    Args:
        tool:     Tool name.
        mkt_root: Marketplace root directory (absolute).
        runner:   Callable(args: list[str], **kwargs) invoked instead of
                  ``subprocess.run``.  Defaults to ``subprocess.run`` with
                  ``check=True``.  Always pass a stub in tests.
    """
    if runner is None:
        runner = lambda args, **kw: subprocess.run(args, check=True, **kw)  # noqa: E731

    runner([
        "claude", "plugin", "marketplace", "add",
        "--scope", "user",
        str(mkt_root),
    ])
    runner([
        "claude", "plugin", "install",
        f"{tool}@trailhead-{tool}",
        "--scope", "user",
    ])


def rewire(
    tool: str,
    mkt_root: Path,
    *,
    runner=None,
) -> None:
    """Refresh an already-registered plugin after recomposition.

    Shells:
      ``claude plugin update <tool>@trailhead-<tool>``

    Args:
        tool:     Tool name.
        mkt_root: Marketplace root directory (unused by the CLI call itself,
                  present for API symmetry with ``register`` and for future
                  per-tool scoping).
        runner:   Injectable runner (same contract as in ``register``).
    """
    if runner is None:
        runner = lambda args, **kw: subprocess.run(args, check=True, **kw)  # noqa: E731

    runner([
        "claude", "plugin", "update",
        f"{tool}@trailhead-{tool}",
    ])
