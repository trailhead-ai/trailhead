"""Uninstall orchestrator for `trailhead uninstall`.

The inverse of `trailhead install`.  Tears down the *wiring* trailhead created,
leaving the user's DATA intact (lore vault, camp groups, and each plugin's
persistent data dir under ~/.claude/plugins/data/ — preserved via the harness
`--keep-data` flag).  A later `trailhead install` re-wires onto that data.

Teardown steps:
  1. Discover wired tools (config capabilities ∪ composed/ trees with a
     registration marker).
  2. Confirm on a TTY (unless --yes).
  3. For each tool: de-register from the harness (best-effort — a tool already
     removed out-of-band must not abort the rest), then delete its composed tree.
  4. Remove PATH integration (rc block + shim dir).
  5. Delete trailhead's own config + state bookkeeping (config.toml,
     update_state.json, lock, empty composed/).

A-9 hygiene mirrors install:
  - progress/summary → stdout, errors/warnings → stderr
  - nonzero exit only on a true failure (a best-effort harness warning is not one)
  - --json machine-readable, --quiet suppresses progress

Concurrency:
  The per-tool teardown mutates the composed/ trees, so it runs under the shared
  `wire_lock` — the same guard update/config-toggle use for composed mutation.
  PATH + config/state cleanup run after the lock is released (deleting the lock
  file while holding it would be self-defeating).

Confirmation:
  Uninstall is destructive (de-registers live plugins, edits the shell rc), so it
  never runs silently.  On an interactive TTY it prompts (bare-enter = No).  When
  it cannot prompt — piped stdin or --json — it refuses unless --yes is passed.

Hermeticity (B-3):
  unregister / remove_path_integration are imported at module level so tests can
  patch them.  The harness-CLI runner is injectable via `runner=`.  _is_tty is a
  thin wrapper patchable in tests.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from trailhead.config import load_config
from trailhead.pathint import remove_path_integration, resolve_shim_dir
from trailhead.paths import config_dir, state_dir
from trailhead.registry import unregister
from trailhead.wire import LockError, wire_lock

_REGISTERED_MARKER = ".trailhead-registered"
_STATE_FILES = ("update_state.json", "trailhead.lock")
_CONFIG_FILENAME = "config.toml"


def _is_tty() -> bool:
    """Return True if stdin is interactive. Thin wrapper so tests can patch it."""
    return sys.stdin.isatty()


# ---------------------------------------------------------------------------
# Public API: run_uninstall
# ---------------------------------------------------------------------------


def run_uninstall(
    *,
    env: dict[str, str] | None = None,
    quiet: bool = False,
    as_json: bool = False,
    assume_yes: bool = False,
    runner=None,
) -> int:
    """Execute the uninstall pipeline. Returns an int exit code.

    Args:
        env:        Env dict for path resolution (hermeticity).
        quiet:      Suppress progress lines (summary still printed).
        as_json:    Print machine-readable JSON instead of a human summary.
        assume_yes: Skip the interactive confirmation prompt.
        runner:     Injectable harness-CLI runner (passed through to unregister).

    Returns:
        0 on success (including best-effort harness warnings), nonzero only on
        a hard failure.
    """
    _env = env if env is not None else dict(os.environ)
    is_tty = _is_tty()

    composed_root = state_dir("trailhead", env=_env) / "composed"
    tools = _discover_wired_tools(_env, composed_root)

    if not tools:
        msg = "nothing to uninstall — no wired tools found"
        if as_json:
            print(json.dumps({"removed": [], "warnings": [], "message": msg}))
        else:
            print(msg)
        return 0

    # ------------------------------------------------------------------
    # Confirmation (destructive, outward-facing teardown of harness state).
    # Never run silently: prompt on a TTY; otherwise require explicit --yes.
    # ------------------------------------------------------------------
    if not assume_yes:
        if is_tty and not as_json:
            tools_str = ", ".join(tools)
            print(
                f"This removes trailhead's wiring for: {tools_str}\n"
                f"  - de-registers the plugins from Claude Code\n"
                f"  - removes the PATH shim dir and shell-rc block\n"
                f"  - deletes trailhead's config + composed trees\n"
                f"Your data is kept (lore vault, camp groups, plugin data dirs).\n"
            )
            if not _confirm("Proceed? [y/N] "):
                print("aborted — nothing was changed")
                return 0
        else:
            # Can't prompt (piped stdin or --json) → refuse rather than tear
            # down silently.  Nothing has been changed at this point.
            print(
                "trailhead: refusing to uninstall without confirmation — re-run "
                "with --yes (uninstall de-registers plugins and removes PATH "
                "integration; your data is kept)",
                file=sys.stderr,
            )
            return 1

    # ------------------------------------------------------------------
    # Per-tool teardown (best-effort harness de-registration).
    # Under wire_lock: mutating composed/ races a concurrent update/config-toggle
    # otherwise.  Config/state cleanup happens after the lock is released.
    # ------------------------------------------------------------------
    removed: list[str] = []
    warnings: list[str] = []

    try:
        with wire_lock(env=_env):
            for tool in tools:
                mkt_root = composed_root / tool
                if not quiet and not as_json:
                    print(f"removing {tool}…")

                try:
                    unregister(tool, mkt_root, runner=runner)
                except Exception as exc:
                    # Best-effort: the plugin may already be gone, or the harness
                    # CLI may be unavailable.  Warn, but keep tearing down state.
                    warnings.append(f"{tool}: harness de-registration warning: {exc}")

                if mkt_root.exists():
                    shutil.rmtree(mkt_root, ignore_errors=True)
                removed.append(tool)
    except LockError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # ------------------------------------------------------------------
    # PATH integration teardown (rc block + shim dir)
    # ------------------------------------------------------------------
    try:
        remove_path_integration(env=_env)
    except Exception as exc:
        warnings.append(f"PATH integration removal warning: {exc}")

    shim_dir = resolve_shim_dir(env=_env)
    if shim_dir.exists():
        shutil.rmtree(shim_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # trailhead's own config + state bookkeeping
    # ------------------------------------------------------------------
    _remove_config_and_state(_env, composed_root)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    for w in warnings:
        print(f"trailhead: {w}", file=sys.stderr)

    if as_json:
        print(json.dumps({"removed": removed, "warnings": warnings}))
    else:
        _print_human_summary(removed, warnings, quiet=quiet)

    return 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _confirm(prompt: str) -> bool:
    """Read a y/N confirmation from stdin. Anything but y/yes is False.

    flush=True is essential: the prompt has no trailing newline, so without an
    explicit flush it sits in the stdout buffer until the next write — the user
    sees a bare cursor, types blind, and the prompt only appears afterwards.
    """
    print(prompt, end="", flush=True)
    try:
        raw = sys.stdin.readline()
    except (EOFError, KeyboardInterrupt):
        return False
    return raw.strip().lower() in ("y", "yes")


def _discover_wired_tools(env: dict[str, str], composed_root: Path) -> list[str]:
    """Return the sorted set of wired tools to tear down.

    Union of:
      - tools declared in config.capabilities, and
      - composed/<tool>/ trees that carry a registration marker.

    The union matters: this machine may have a composed tree from a direct
    wire() with no config.toml, or a config with a composed tree already
    removed.  Either way we want to clean up everything trailhead touched.
    """
    tools: set[str] = set()

    cfg = load_config(env=env)
    tools.update(cfg.capabilities.keys())

    if composed_root.is_dir():
        for child in composed_root.iterdir():
            if child.is_dir() and (child / _REGISTERED_MARKER).exists():
                tools.add(child.name)

    return sorted(tools)


def _remove_config_and_state(env: dict[str, str], composed_root: Path) -> None:
    """Delete config.toml + state bookkeeping; remove composed/ if now empty."""
    config_path = config_dir("trailhead", env=env) / _CONFIG_FILENAME
    config_path.unlink(missing_ok=True)

    _state_dir = state_dir("trailhead", env=env)
    for name in _STATE_FILES:
        (_state_dir / name).unlink(missing_ok=True)

    # Drop composed/ if it's now empty (all tool trees removed).
    if composed_root.is_dir() and not any(composed_root.iterdir()):
        composed_root.rmdir()


def _print_human_summary(removed: list[str], warnings: list[str], *, quiet: bool) -> None:
    """Print the uninstall summary."""
    lines = []
    if removed:
        lines.append("uninstalled:")
        for tool in removed:
            lines.append(f"  {tool}")
        lines.append("")
    lines.append("removed trailhead's PATH integration and config; your data was kept")
    lines.append("")
    lines.append("start a fresh Claude Code session so the harness drops the plugins")
    print("\n".join(lines))
