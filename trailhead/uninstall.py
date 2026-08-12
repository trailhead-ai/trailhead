"""Uninstall orchestrator for `trailhead uninstall`.

The inverse of `trailhead install`, but NOT fine-tunable: it always nukes the
entire install — every harness's plugins, both CLIs, the PATH integration, and
the composed trees.  The user's DATA is kept (lore vault, camp groups, and each
plugin's persistent data dir under the harness, preserved via the harness
`--keep-data` flag).  A later `trailhead install` re-wires onto that data.

Discovery is purely on-disk (no config dependency): each harness composed under
``state_dir/composed/<harness>/`` is torn down, with its installed tools read
through the harness seam (``Harness.installed_tools``) rather than by re-deriving
any on-disk marker scheme here.

Teardown steps:
  1. Discover harness composed trees + their installed tools.
  2. Confirm on a TTY (unless --yes).
  3. Per harness (under the wire lock): unregister each installed tool, unregister
     the marketplace, then delete the harness composed tree.
  4. Remove PATH integration (rc block + shim dir).
  5. Delete trailhead's leftover state (empty composed/ dir).

Output hygiene mirrors install. Best-effort harness warnings do not fail the run.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from trailhead.harness import HarnessError, get_harness
from trailhead.pathint import resolve_shim_dir
from trailhead.paths import state_dir
from trailhead.wire import LockError, wire_lock


def _is_tty() -> bool:
    """Return True if stdin is interactive. Thin wrapper so tests can patch it."""
    return sys.stdin.isatty()


def run_uninstall(
    *,
    env: dict[str, str] | None = None,
    quiet: bool = False,
    as_json: bool = False,
    assume_yes: bool = False,
    runner=None,
) -> int:
    """Execute the (nuke-everything) uninstall pipeline. Returns an int exit code."""
    _env = env if env is not None else dict(os.environ)
    is_tty = _is_tty()

    composed_base = state_dir("trailhead", env=_env) / "composed"
    discovered = _discover_harness_names(composed_base)
    shim_dir = resolve_shim_dir(env=_env)
    has_pathint = shim_dir.exists()

    if not discovered and not has_pathint:
        msg = "nothing to uninstall — no installed harnesses found"
        if as_json:
            print(json.dumps({"removed": {}, "warnings": [], "message": msg}))
        else:
            print(msg)
        return 0

    # ------------------------------------------------------------------
    # Confirmation (destructive teardown of harness state).
    # ------------------------------------------------------------------
    if not assume_yes:
        if is_tty and not as_json:
            harness_list = ", ".join(discovered) or "(none)"
            print(
                f"This removes the ENTIRE trailhead install:\n"
                f"  - de-registers all plugins from: {harness_list}\n"
                f"  - removes the camp/lore CLI shim dir "
                f"(then drop the `shellenv` line from your profile)\n"
                f"  - deletes trailhead's composed trees\n"
                f"Your data is kept (lore vault, camp groups, plugin data dirs).\n"
            )
            if not _confirm("Proceed? [y/N] "):
                print("aborted — nothing was changed")
                return 0
        else:
            print(
                "trailhead: refusing to uninstall without confirmation — re-run "
                "with --yes (uninstall removes ALL plugins + the CLIs; your data "
                "is kept)",
                file=sys.stderr,
            )
            return 1

    # ------------------------------------------------------------------
    # Per-harness teardown (best-effort harness de-registration), under lock.
    # ------------------------------------------------------------------
    removed: dict[str, list[str]] = {}
    warnings: list[str] = []

    try:
        with wire_lock(env=_env):
            for hname in discovered:
                composed_root = composed_base / hname

                harness = None
                try:
                    harness = get_harness(hname)
                except HarnessError:
                    warnings.append(
                        f"{hname}: unknown harness — deleting its tree without CLI de-registration"
                    )

                # Installed tools are read through the harness seam; an unknown
                # harness can't be introspected, so it tears down with no tool list.
                tools = harness.installed_tools(composed_root) if harness is not None else []
                if not quiet and not as_json:
                    print(f"removing {hname}: {', '.join(tools) or '(no tools)'}…")

                if harness is not None:
                    for tool in tools:
                        try:
                            harness.unregister_tool(tool, composed_root, runner=runner)
                        except Exception as exc:
                            warnings.append(f"{hname}/{tool}: de-registration warning: {exc}")
                    if harness.is_registered(composed_root):
                        try:
                            harness.unregister_marketplace(composed_root, runner=runner)
                        except Exception as exc:
                            warnings.append(f"{hname}: marketplace de-registration warning: {exc}")

                if composed_root.exists():
                    shutil.rmtree(composed_root, ignore_errors=True)
                removed[hname] = tools
    except LockError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # ------------------------------------------------------------------
    # CLI shim teardown (the shim dir; trailhead never wrote your shell rc, so
    # there's nothing to strip there — just drop the `… shellenv` line yourself).
    # ------------------------------------------------------------------
    if shim_dir.exists():
        shutil.rmtree(shim_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Leftover state: drop the composed base dir if now empty.
    # ------------------------------------------------------------------
    if composed_base.is_dir() and not any(composed_base.iterdir()):
        composed_base.rmdir()

    for w in warnings:
        print(f"trailhead: {w}", file=sys.stderr)

    if as_json:
        print(json.dumps({"removed": removed, "warnings": warnings}))
    else:
        _print_human_summary(removed, quiet=quiet)

    return 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _confirm(prompt: str) -> bool:
    """Read a y/N confirmation from stdin. Anything but y/yes is False."""
    print(prompt, end="", flush=True)
    try:
        raw = sys.stdin.readline()
    except (EOFError, KeyboardInterrupt):
        return False
    return raw.strip().lower() in ("y", "yes")


def _discover_harness_names(composed_base: Path) -> list[str]:
    """Return the sorted names of harness composed dirs under ``composed_base``.

    Enumerates the composed tree names only (core layout); each harness's installed
    tools are read later through the harness seam (``Harness.installed_tools``), not
    by re-deriving the on-disk marker scheme here.  A harness dir with no install
    markers still appears so its tree + marketplace registration are torn down.
    """
    if not composed_base.is_dir():
        return []
    return sorted(hdir.name for hdir in composed_base.iterdir() if hdir.is_dir())


def _print_human_summary(removed: dict[str, list[str]], *, quiet: bool) -> None:
    lines: list[str] = []
    if removed:
        lines.append("uninstalled:")
        for hname, tools in removed.items():
            lines.append(f"  {hname}: {', '.join(tools) or '(no tools)'}")
        lines.append("")
    lines.append("removed the camp/lore CLI shims; your data was kept")
    lines.append(
        "bare-name `trailhead` still resolves in shells that already eval'd the "
        "`… shellenv` line — remove that line from your shell profile to drop it"
    )
    lines.append("re-install later with the full path: <checkout>/bin/trailhead install")
    lines.append("")
    lines.append("start a fresh Claude Code session so the harness drops the plugins")
    print("\n".join(lines))
