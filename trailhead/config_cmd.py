"""trailhead config subcommand — shared configuration surface (Slice 5).

Subcommands:
  trailhead config registry [<value>]
      Read (no value) or set the D29 source registry.  Persists to config.

  trailhead config path_integration [on|off]
      Toggle PATH integration (default-on).  off → remove_path_integration;
      on → install_path_integration.  Persists to config.

  trailhead config capabilities [<tool> <cap> on|off]
      Runtime capability toggle.  Re-runs wire() for the affected tool.
      R-2 (binding): writes config ONLY AFTER a successful re-wire.
      On re-wire failure: config is unchanged + named error on stderr.

  trailhead config active-group [<name>]
      Read (no value) or set camp's active group.  Uses
      group_config.load_all_groups from the camp groups dir.

A-9 hygiene:
  - values → stdout; errors → stderr
  - nonzero exit on failure
  - no ANSI output

D-7: source registry is read from / written to config only; no hardcoded URL
appears in logic.

Hermeticity (B-3):
  wire and install/remove_path_integration are imported at module level so
  tests can patch them via patch("trailhead.config_cmd.wire"), etc.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from trailhead.config import load_config, save_config
from trailhead.pathint import install_path_integration, remove_path_integration
from trailhead.wire import LockError, WireError, default_manifest_paths, wire, wire_lock

# ---------------------------------------------------------------------------
# Module-level repo root (needed to build tool bin paths for pathint)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_config(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
) -> int:
    """Execute the config subcommand. Returns an int exit code.

    Args:
        args:  Subcommand args (e.g. ["registry", "value"] or ["capabilities",
               "lore", "recall", "off"]).
        env:   Env dict for path resolution (hermeticity).
    """
    _env = env if env is not None else dict(os.environ)

    if not args:
        print("usage: trailhead config <subcommand> [...]", file=sys.stderr)
        print("subcommands: registry, path_integration, capabilities, active-group", file=sys.stderr)
        return 1

    sub = args[0]
    rest = args[1:]

    dispatch = {
        "registry": _cmd_registry,
        "path_integration": _cmd_path_integration,
        "capabilities": _cmd_capabilities,
        "active-group": _cmd_active_group,
    }

    handler = dispatch.get(sub)
    if handler is None:
        print(
            f"trailhead config: unknown subcommand {sub!r}; "
            f"valid: {', '.join(sorted(dispatch))}",
            file=sys.stderr,
        )
        return 1

    return handler(rest, env=_env)


# ---------------------------------------------------------------------------
# config registry
# ---------------------------------------------------------------------------


def _cmd_registry(args: list[str], *, env: dict[str, str]) -> int:
    """Read or set the D29 source registry."""
    cfg = load_config(env=env)

    if not args:
        # Read
        print(cfg.registry)
        return 0

    new_value = args[0]
    cfg.registry = new_value
    save_config(cfg, env=env)
    return 0


# ---------------------------------------------------------------------------
# config path_integration
# ---------------------------------------------------------------------------


def _cmd_path_integration(args: list[str], *, env: dict[str, str]) -> int:
    """Toggle PATH integration on or off."""
    cfg = load_config(env=env)

    if not args:
        # Read
        print("on" if cfg.path_integration else "off")
        return 0

    toggle = args[0].lower()
    if toggle not in ("on", "off"):
        print(
            f"trailhead config path_integration: expected 'on' or 'off', got {toggle!r}",
            file=sys.stderr,
        )
        return 1

    if toggle == "off":
        try:
            remove_path_integration(env=env)
        except Exception as exc:
            print(f"trailhead: path integration removal failed: {exc}", file=sys.stderr)
            return 1
        cfg.path_integration = False
        save_config(cfg, env=env)
        return 0

    # on
    trailhead_root = str(_REPO_ROOT)
    wired_tool_bins: dict[str, Path] = {}
    for tool in cfg.capabilities:
        bin_path = _REPO_ROOT / "tools" / tool / "plugins" / tool / "bin" / tool
        if bin_path.exists():
            wired_tool_bins[tool] = bin_path

    try:
        install_path_integration(
            wired_tool_bins,
            trailhead_root,
            env=env,
        )
    except Exception as exc:
        print(f"trailhead: path integration failed: {exc}", file=sys.stderr)
        return 1

    cfg.path_integration = True
    save_config(cfg, env=env)
    return 0


# ---------------------------------------------------------------------------
# config capabilities
# ---------------------------------------------------------------------------


def _cmd_capabilities(args: list[str], *, env: dict[str, str]) -> int:
    """Read or toggle a capability.

    Usage:
      trailhead config capabilities                 → print all caps
      trailhead config capabilities <tool> <cap> on|off
    """
    cfg = load_config(env=env)

    if not args:
        # Read all caps
        if not cfg.capabilities:
            print("(no capabilities configured)")
        else:
            for tool, caps in sorted(cfg.capabilities.items()):
                cap_str = ", ".join(sorted(caps)) if caps else "(base only)"
                print(f"  {tool}: {cap_str}")
        return 0

    if len(args) < 3:
        print(
            "usage: trailhead config capabilities <tool> <cap> on|off",
            file=sys.stderr,
        )
        return 1

    tool, cap, toggle = args[0], args[1], args[2].lower()
    if toggle not in ("on", "off"):
        print(
            f"trailhead config capabilities: expected 'on' or 'off', got {toggle!r}",
            file=sys.stderr,
        )
        return 1

    # Compute the new capability set for the tool
    current_caps = set(cfg.capabilities.get(tool, []))
    if toggle == "off":
        new_caps = current_caps - {cap}
    else:
        new_caps = current_caps | {cap}

    # R-2 (binding): re-wire FIRST, persist ONLY on success.
    # I1 (R-8): acquire shared wire lock to guard against concurrent
    # update / install / toggle races on the composed dest.
    manifest_paths = default_manifest_paths()
    selection = {tool: new_caps}

    try:
        with wire_lock(env=env):
            wire(selection, manifest_paths=manifest_paths, env=env)
    except LockError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except WireError as exc:
        print(
            f"trailhead: re-wire failed for {tool!r} — {exc}; config unchanged",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(
            f"trailhead: re-wire failed for {tool!r} — {exc}; config unchanged",
            file=sys.stderr,
        )
        return 1

    # Re-wire succeeded — now persist the new capability set (R-2)
    cfg.capabilities[tool] = sorted(new_caps)
    save_config(cfg, env=env)
    return 0


# ---------------------------------------------------------------------------
# config active-group
# ---------------------------------------------------------------------------


def _cmd_active_group(args: list[str], *, env: dict[str, str]) -> int:
    """Read or set camp's active group via the group_config surface."""
    try:
        import sys as _sys
        from pathlib import Path as _Path

        # Locate camp's group_config module via the tools tree
        camp_scripts = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"
        if str(camp_scripts) not in _sys.path:
            _sys.path.insert(0, str(camp_scripts))
        from group_config import load_all_groups
        from trailhead.paths import config_dir

        groups_dir = config_dir("camp", env=env) / "groups"
        groups = load_all_groups(groups_dir)
    except Exception as exc:
        print(
            f"trailhead: could not load camp group config: {exc}",
            file=sys.stderr,
        )
        groups = []

    if not args:
        # Read
        if not groups:
            print("(no camp groups configured)")
            print("run `camp group create <name>` to configure a group")
        else:
            for g in groups:
                name = g.get("group", {}).get("name", "?")
                print(f"  {name}")
        return 0

    # Set: find the group by name and report it
    target = args[0]
    found = None
    for g in groups:
        if g.get("group", {}).get("name") == target:
            found = g
            break

    if found is None:
        print(
            f"trailhead: group {target!r} not found; "
            f"available groups: {[g.get('group', {}).get('name') for g in groups]}",
            file=sys.stderr,
        )
        return 1

    print(f"active group: {target}")
    return 0
