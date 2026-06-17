"""Install orchestrator for `trailhead install`.

Config-driven, non-interactive, multi-harness:

  1. Detect harnesses on the machine (e.g. ~/.claude → claude_code).
  2. Resolve the effective config (config file + CLI overrides) → which harnesses,
     which plugins (subagents/skills + overrides), and the camp/lore CLI flags.
  3. For each resolved harness: compose the selected plugins and install them via
     the harness (wire + harness registration tail), under the wire lock.
  4. Build the camp/lore CLI shim dir (harness-independent, additive). trailhead
     does NOT edit your shell rc — it tells you to add `eval "$(… shellenv)"`.
  5. Print the summary.

No presets, no interactive prompts, no remote fetch, no install manifest — the
repo checkout IS the source ("install = clone the repo").

Upgrades are additive: re-running install only adds; it never removes a plugin
or CLI shim that a previous run installed.

No harness found (and none named): warn, still build the CLI shims, exit non-zero.

Hermeticity (B-3): detect_harnesses / wire / create_shims / get_harness are
imported at module level so tests can patch them.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from trailhead.compose import UnknownSkillError, UnknownSubagentError
from trailhead.harness import detect_harnesses, get_harness
from trailhead.install_config import (
    ConfigResolveError,
    resolve_config,
    resolve_config_path,
)
from trailhead.pathint import create_shims
from trailhead.wire import LockError, WireError, wire, wire_lock

_REPO_ROOT = Path(__file__).parent.parent
_TRAILHEAD_BIN = _REPO_ROOT / "bin" / "trailhead"

# CLI binaries shipped by the camp/lore plugins, keyed by the install flag.
_CAMP_BIN = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp"
_LORE_BIN = _REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / "bin" / "lore"


def run_install(
    *,
    config_arg: str | None = None,
    harnesses: list[str] | None = None,
    plugins: list[str] | None = None,
    no_camp: bool = False,
    no_lore: bool = False,
    env: dict[str, str] | None = None,
    quiet: bool = False,
    as_json: bool = False,
    runner=None,
) -> int:
    """Execute the install pipeline. Returns an int exit code.

    Returns 0 on success, 1 on failure or when no harness was found.
    """
    _env = env if env is not None else dict(os.environ)

    # ------------------------------------------------------------------
    # Resolve config (file + CLI overrides + detection)
    # ------------------------------------------------------------------
    detected = [h.name for h in detect_harnesses(_env)]
    config_path = resolve_config_path(config_arg, _REPO_ROOT)
    try:
        cfg = resolve_config(
            config_path=config_path,
            cli_harnesses=harnesses,
            cli_plugins=plugins,
            no_camp=no_camp,
            no_lore=no_lore,
            detected_harnesses=detected,
        )
    except (ConfigResolveError, UnknownSubagentError, UnknownSkillError) as exc:
        print(f"trailhead: {exc}", file=sys.stderr)
        return 1

    # ------------------------------------------------------------------
    # Wire plugins into each resolved harness (under the shared lock)
    # ------------------------------------------------------------------
    wired: dict[str, list[str]] = {}
    if cfg.harnesses:
        try:
            with wire_lock(env=_env):
                for rh in cfg.harnesses:
                    harness = get_harness(rh.name)
                    plugin_names = [p.name for p in rh.plugins]
                    if not quiet and not as_json:
                        print(
                            f"installing into {rh.name}: "
                            f"{', '.join(plugin_names) or '(no plugins)'}…"
                        )
                    wire(rh.selection(), harness=harness, env=_env, runner=runner)
                    wired[rh.name] = plugin_names
        except LockError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except WireError as exc:
            print(f"trailhead: {exc}", file=sys.stderr)
            return 1

    # ------------------------------------------------------------------
    # Build the camp/lore CLI shim dir (harness-independent, additive).
    # The shim dir's contents encode the selection; `shellenv` adds it to PATH.
    # ------------------------------------------------------------------
    cli_tools: dict[str, Path] = {}
    if cfg.install_camp_cli and _CAMP_BIN.exists():
        cli_tools["camp"] = _CAMP_BIN
    if cfg.install_lore_cli and _LORE_BIN.exists():
        cli_tools["lore"] = _LORE_BIN

    shim_dir = None
    if cli_tools:
        try:
            shim_dir = create_shims(cli_tools, str(_REPO_ROOT), env=_env).shim_dir
        except Exception as exc:
            # M1: a shim-dir failure is a warning — wiring succeeded.
            print(
                f"trailhead: could not build the CLI shim dir: {exc}\n"
                f"  (the plugins are installed; the camp/lore CLIs just aren't shimmed)",
                file=sys.stderr,
            )

    no_harness = not cfg.harnesses

    if as_json:
        _print_json_summary(cfg, wired, shim_dir, no_harness=no_harness)
    else:
        _print_human_summary(cfg, wired, shim_dir, no_harness=no_harness)

    if no_harness:
        print(
            "trailhead: no code harness detected (looked for ~/.claude). "
            "Built the CLI shims only — re-run with `--harness <name>` "
            "(e.g. claude_code) to install the agent-plugins.",
            file=sys.stderr,
        )
        return 1

    return 0


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


def _print_human_summary(cfg, wired, shim_dir, *, no_harness: bool) -> None:
    lines: list[str] = []

    if wired:
        lines.append("installed plugins:")
        for harness_name, plugin_names in wired.items():
            lines.append(f"  {harness_name}: {', '.join(plugin_names) or '(none)'}")
        lines.append("")

    clis = []
    if cfg.install_camp_cli:
        clis.append("camp")
    if cfg.install_lore_cli:
        clis.append("lore")
    if shim_dir is not None and clis:
        lines.append(f"CLIs ({', '.join(clis)}): shims in {shim_dir}")
        lines.append("  to put them on your PATH, add this to your shell profile:")
        lines.append(f'    eval "$({_TRAILHEAD_BIN} shellenv)"')
        lines.append("  then restart your shell (or re-eval it in the current one)")
        lines.append("")

    if not no_harness:
        lines.append("start a fresh Claude Code session to load the installed plugins")

    print("\n".join(lines).rstrip())


def _print_json_summary(cfg, wired, shim_dir, *, no_harness: bool) -> None:
    data = {
        "harnesses": wired,
        "install_camp_cli": cfg.install_camp_cli,
        "install_lore_cli": cfg.install_lore_cli,
        "shim_dir": str(shim_dir) if shim_dir else None,
        "shellenv": f'eval "$({_TRAILHEAD_BIN} shellenv)"',
        "no_harness": no_harness,
    }
    print(json.dumps(data))
