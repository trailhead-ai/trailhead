"""Harness-registration concern for trailhead composed plugins.

This module owns the narrow responsibility of registering a composed
trailhead plugin tree with the Claude Code harness marketplace system.

Architecture
------------
``registry.py`` is **not** a planner or writer — it owns only:

1. ``generate_marketplace_json`` — writes ONE Shape-A marketplace.json
   at ``<composed_root>/.claude-plugin/marketplace.json``, named
   ``trailhead``, with one ``plugins[]`` entry per tool.  The write is
   **atomic** (temp-file + ``os.replace()``) so a torn write can never
   leave the shared file in an invalid state.
2. ``register_marketplace`` — shells ``claude plugin marketplace add
   --scope user <composed_root>`` (idempotent per U-1(d)) and writes the
   global ``<composed_root>/.trailhead-registered`` marker only after
   success.
3. ``install_tool`` — shells ``claude plugin install <tool>@trailhead
   --scope user`` and writes the per-tool
   ``<composed_root>/.trailhead-installed-<tool>`` marker on success.
4. ``rewire_tool`` — refreshes an already-installed tool via
   **uninstall + install** (NOT ``plugin update`` — U-1(e): version-keyed
   and keeps stale content at a static version).  Clears the per-tool
   marker before the pair; rewrites it only after install succeeds (C-2
   self-heal).

Marker layout (split markers)
------------------------------
Markers live in ``composed_root``, NOT inside ``plugins/<tool>/``, which
the atomic promote ``rmtree``s on every re-wire.

- Global: ``<composed_root>/.trailhead-registered``
- Per-tool: ``<composed_root>/.trailhead-installed-<tool>``

The global marker is a skip-optimisation for ``register_marketplace``
(the call itself is idempotent per U-1(d)).  The per-tool marker is a
C-2 self-heal signal for the ``wire()`` loop: a missing marker means
``install_tool`` should run; a present marker means ``rewire_tool``.

Input guard
-----------
Every ``tool`` value is validated against ``^[a-z][a-z0-9_-]*$`` before
it reaches any CLI arg, marker filename, or ``source`` path.

Hermeticity contract (B-3)
--------------------------
The CLI invocation is injectable via a ``runner=`` keyword argument.
Tests ALWAYS pass a stub runner and NEVER invoke the real ``claude plugin``
CLI against the user's harness.  The default runner is ``subprocess.run``
with ``check=True``; it is only exercised in live-session dogfood runs.

registry.py NEVER writes to ``~/.claude/plugins/`` directly — the harness
CLI manages ``known_marketplaces.json`` and the plugin cache; registry only
generates ``marketplace.json`` under the ``state_dir``-rooted
``composed_root``.

Shape-A marketplace.json
-------------------------
The generated marketplace.json follows Shape A (validated live via
``claude plugin validate``):

    {
      "name": "trailhead",
      "owner": {"name": "trailhead"},
      "description": "...",
      "plugins": [
        {
          "name": "<tool>",
          "source": "./plugins/<tool>",
          "description": "..."
        },
        ...
      ]
    }

All tools share one marketplace root (``composed_root``); plugin trees
live at ``composed_root/plugins/<tool>/``.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


_TOOL_NAME_RE = re.compile(r'^[a-z][a-z0-9_-]*$')

_REGISTERED_MARKER = ".trailhead-registered"
_INSTALLED_MARKER_PREFIX = ".trailhead-installed-"

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


def _tool_description(tool: str) -> str:
    return _TOOL_DESCRIPTIONS.get(
        tool,
        f"Trailhead-composed plugin for {tool}.",
    )


def _validate_tool(tool: str) -> None:
    """Raise ValueError if tool does not match ^[a-z][a-z0-9_-]*$."""
    if not isinstance(tool, str) or not _TOOL_NAME_RE.match(tool):
        raise ValueError(
            f"Invalid tool name {tool!r}: must match ^[a-z][a-z0-9_-]*$"
        )


def _default_runner(args, **kw):
    return subprocess.run(args, check=True, **kw)


def generate_marketplace_json(tools: list[str], composed_root: Path) -> None:
    """Write ONE consolidated Shape-A marketplace.json at composed_root/.claude-plugin/.

    The marketplace is named ``trailhead`` and has one ``plugins[]`` entry per tool.
    Plugin order is deterministic (sorted).  The write is atomic: rendered to a
    sibling temp file first, then ``os.replace()``-ed into place.

    Args:
        tools:         List of tool names.  Each must match ^[a-z][a-z0-9_-]*$.
        composed_root: Shared marketplace root directory.  ``plugins/<tool>/``
                       under this root is where compose writes plugin trees.
    """
    for tool in tools:
        _validate_tool(tool)

    claude_plugin_dir = composed_root / ".claude-plugin"
    claude_plugin_dir.mkdir(parents=True, exist_ok=True)

    marketplace = {
        "name": "trailhead",
        "owner": {"name": "trailhead"},
        "description": "Trailhead-composed plugin marketplace.",
        "plugins": [
            {
                "name": tool,
                "source": f"./plugins/{tool}",
                "description": _tool_description(tool),
            }
            for tool in sorted(tools)
        ],
    }

    out = claude_plugin_dir / "marketplace.json"
    # Atomic write: render to a sibling temp file, then os.replace() into place.
    # A crash mid-write can never leave a torn shared marketplace.json.
    fd, tmp_path = tempfile.mkstemp(
        dir=claude_plugin_dir, prefix=".marketplace-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(marketplace, indent=2))
        os.replace(tmp_path, out)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def register_marketplace(
    composed_root: Path,
    *,
    runner=None,
) -> None:
    """Register the consolidated trailhead marketplace via the harness CLI.

    Shells:
      ``claude plugin marketplace add --scope user <composed_root>``

    Idempotent per U-1(d) — safe to call every wire regardless of the global
    marker.  Writes the global ``<composed_root>/.trailhead-registered`` marker
    only after the CLI call succeeds (skip-optimisation for future wires).

    Args:
        composed_root: Shared marketplace root directory (absolute).
        runner:        Callable(args: list[str], **kwargs).  Defaults to
                       ``subprocess.run`` with ``check=True``.  Always pass
                       a stub in tests (B-3).
    """
    if runner is None:
        runner = _default_runner

    runner([
        "claude", "plugin", "marketplace", "add",
        "--scope", "user",
        str(composed_root),
    ])
    (composed_root / _REGISTERED_MARKER).write_text("{}")


def install_tool(
    tool: str,
    composed_root: Path,
    *,
    runner=None,
) -> None:
    """Install a tool from the consolidated trailhead marketplace.

    Shells:
      ``claude plugin install <tool>@trailhead --scope user``

    Writes the per-tool ``<composed_root>/.trailhead-installed-<tool>`` marker
    only after the CLI call succeeds.

    Args:
        tool:          Tool name (must match ^[a-z][a-z0-9_-]*$).
        composed_root: Shared marketplace root directory (absolute).
        runner:        Injectable runner (B-3 contract).
    """
    _validate_tool(tool)

    if runner is None:
        runner = _default_runner

    runner([
        "claude", "plugin", "install",
        f"{tool}@trailhead",
        "--scope", "user",
    ])
    (composed_root / f"{_INSTALLED_MARKER_PREFIX}{tool}").write_text("{}")


def rewire_tool(
    tool: str,
    composed_root: Path,
    *,
    runner=None,
) -> None:
    """Refresh an already-installed tool after recomposition.

    Sequence: **uninstall THEN install** (NOT ``plugin update`` — U-1(e):
    ``plugin update`` is version-keyed and keeps stale content when the
    version is static).

    Shells:
      1. ``claude plugin uninstall <tool>@trailhead --scope user``
         (tolerates "not installed" — the install must still run)
      2. ``claude plugin install <tool>@trailhead --scope user``

    Clears the per-tool marker before the pair; rewrites it only after
    install succeeds (C-2 self-heal — a failure mid-pair leaves the marker
    absent so the next wire re-attempts cleanly).

    Args:
        tool:          Tool name (must match ^[a-z][a-z0-9_-]*$).
        composed_root: Shared marketplace root directory.
        runner:        Injectable runner (B-3 contract).
    """
    _validate_tool(tool)

    if runner is None:
        runner = _default_runner

    marker = composed_root / f"{_INSTALLED_MARKER_PREFIX}{tool}"
    # Clear before the CLI pair (C-2: failure leaves marker absent).
    marker.unlink(missing_ok=True)

    try:
        runner([
            "claude", "plugin", "uninstall",
            f"{tool}@trailhead",
            "--scope", "user",
        ])
    except Exception:
        # Tolerate "not installed" — install must still run.
        pass

    runner([
        "claude", "plugin", "install",
        f"{tool}@trailhead",
        "--scope", "user",
    ])

    marker.write_text("{}")


def unregister_tool(
    tool: str,
    composed_root: Path,
    *,
    runner=None,
) -> None:
    """Uninstall ONE tool from the consolidated trailhead marketplace.

    The per-tool inverse of ``install_tool``.  Shells:
      ``claude plugin uninstall <tool>@trailhead --scope user --keep-data --yes``

    ``--keep-data`` preserves the plugin's persistent data dir
    (``~/.claude/plugins/data/{id}/``) so an uninstall is *wiring only* — the
    user's captured notes / group config survive a later reinstall.  ``--yes``
    keeps it non-interactive.

    Does **NOT** remove the marketplace — that is shared across all tools and
    is torn down once by ``unregister_marketplace`` after the last tool.  The
    per-tool ``.trailhead-installed-<tool>`` marker is cleared in ``finally``
    so a torn-down tree never reads as installed afterwards, even if the CLI
    call raises.

    Args:
        tool:          Tool name (must match ^[a-z][a-z0-9_-]*$).
        composed_root: Shared marketplace root directory.
        runner:        Injectable runner (B-3 contract).
    """
    _validate_tool(tool)

    if runner is None:
        runner = _default_runner

    try:
        runner([
            "claude", "plugin", "uninstall",
            f"{tool}@trailhead",
            "--scope", "user",
            "--keep-data",
            "--yes",
        ])
    finally:
        (composed_root / f"{_INSTALLED_MARKER_PREFIX}{tool}").unlink(missing_ok=True)


def unregister_marketplace(
    composed_root: Path,
    *,
    runner=None,
) -> None:
    """Remove the shared ``trailhead`` marketplace (inverse of register_marketplace).

    Called **once** after every tool has been uninstalled — NEVER per-tool, since
    a single marketplace is shared across all tools (removing it per-tool would
    de-register the others).  Shells:
      ``claude plugin marketplace remove trailhead --scope user``

    Clears the global ``.trailhead-registered`` marker in ``finally`` so a
    half-removed state never reads as registered.

    Args:
        composed_root: Shared marketplace root directory.
        runner:        Injectable runner (B-3 contract).
    """
    if runner is None:
        runner = _default_runner

    try:
        runner([
            "claude", "plugin", "marketplace", "remove",
            "trailhead",
            "--scope", "user",
        ])
    finally:
        (composed_root / _REGISTERED_MARKER).unlink(missing_ok=True)
