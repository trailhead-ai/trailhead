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
   plugin.  Writes a ``.trailhead-registered`` sentinel under ``mkt_root``
   **only after both CLI calls succeed** (C-2 registration-state marker).
3. ``rewire`` — shells the harness CLI (``claude plugin update``) to refresh
   an already-registered plugin after recomposition.  Refreshes the
   ``.trailhead-registered`` sentinel on success; removes it if the update
   fails (so the next ``wire`` call re-attempts ``register`` rather than
   calling ``update`` again on a potentially broken state).

Registration-state marker (C-2)
--------------------------------
The file ``<mkt_root>/.trailhead-registered`` is written **only** after
``register`` completes both CLI steps without error.  ``wire.py`` keys the
register-vs-rewire decision on this marker (not on dir existence) so that a
tool whose plugin tree exists but was never fully installed self-heals on the
next ``wire`` call (re-attempts ``register`` instead of calling ``plugin
update`` on a never-installed plugin, which would wedge forever).

``rewire`` clears the marker before invoking the CLI and re-writes it after
success, so a failed ``plugin update`` leaves the marker absent and triggers
a fresh ``register`` path on the next run.

Live-dogfood residual
---------------------
The exact behaviour of ``claude plugin marketplace add`` when the marketplace
is already registered (idempotent? error? silent?) and of ``claude plugin
install`` on a re-run can only be confirmed in a live harness session — these
calls are always stubbed in tests.  The marker design is defensive: it makes
the register-vs-rewire decision robust regardless of harness CLI idempotency.

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


_REGISTERED_MARKER = ".trailhead-registered"


_TOOL_DESCRIPTIONS: dict[str, str] = {
    "lore": (
        "Portable knowledge-management plugin: session lifecycle, "
        "capture skills, and vault recall."
    ),
    "camp": (
        "Portable worktree-orchestration plugin: group config, "
        "dev-env orchestration, and worktree management."
    ),
    "craft": (
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
        tool:     Tool name (e.g. "lore", "camp", "craft").
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

    Writes ``<mkt_root>/.trailhead-registered`` only after both CLI steps
    succeed (C-2 registration-state marker).  The marker is absent if either
    step raises, so a later ``wire`` call can re-attempt registration instead
    of calling ``plugin update`` on a never-installed plugin.

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
    # Both CLI steps succeeded — write the registration-state marker (C-2).
    (mkt_root / _REGISTERED_MARKER).write_text("{}")


def rewire(
    tool: str,
    mkt_root: Path,
    *,
    runner=None,
) -> None:
    """Refresh an already-registered plugin after recomposition.

    Shells:
      ``claude plugin update <tool>@trailhead-<tool>``

    Clears the ``<mkt_root>/.trailhead-registered`` marker before invoking
    the CLI and re-writes it on success (C-2).  A failed update leaves the
    marker absent, so the next ``wire`` call falls back to ``register`` rather
    than looping on a broken ``plugin update``.

    Args:
        tool:     Tool name.
        mkt_root: Marketplace root directory (used to manage the
                  registration-state marker; the CLI call itself does not
                  need the path).
        runner:   Injectable runner (same contract as in ``register``).
    """
    if runner is None:
        runner = lambda args, **kw: subprocess.run(args, check=True, **kw)  # noqa: E731

    # Clear marker before the CLI call; re-written only on success (C-2).
    marker = mkt_root / _REGISTERED_MARKER
    marker.unlink(missing_ok=True)

    runner([
        "claude", "plugin", "update",
        f"{tool}@trailhead-{tool}",
    ])

    marker.write_text("{}")
