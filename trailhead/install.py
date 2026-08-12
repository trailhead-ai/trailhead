"""Install orchestrator for `trailhead install`.

Config-driven, non-interactive, multi-harness:

  1. Detect harnesses on the machine (e.g. ~/.claude → claude_code).
  2. Resolve the effective config (config file + CLI overrides) → which harnesses,
     which plugins (subagents/skills + overrides), and the per-tool CLI flags.
  3. For each resolved harness: compose the selected plugins and install them via
     the harness (wire + harness registration tail), under the wire lock.
  4. Build the CLI shim dir (harness-independent, additive) for every CLI-bearing
     tool (any tool whose manifest declares `cli_bin`) whose flag is enabled.
     trailhead does NOT edit your shell rc — it tells you to add
     `eval "$(… shellenv)"`.
  5. Bootstrap the lore machine via `lore init` (non-interactive + idempotent):
     vault + global index + write-protection guardrail + agent rules. A failed
     bootstrap propagates as a non-zero install exit with the lore stderr — it is
     never swallowed. Harness-agnostic (Axiom 1): no harness-specific branching.
  6. Print the summary.

No presets, no interactive prompts, no remote fetch, no install manifest — the
repo checkout IS the source ("install = clone the repo").

Upgrades are additive: re-running install only adds; it never removes a plugin
or CLI shim that a previous run installed.

No harness found (and none named): warn, still build the CLI shims, exit non-zero.

Hermeticity: detect_harnesses / wire / create_shims / get_harness are
imported at module level so tests can patch them.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from trailhead.capabilities import cli_bearing_manifests
from trailhead.compose import UnknownSkillError, UnknownSubagentError
from trailhead.harness import detect_harnesses, get_harness
from trailhead.install_config import (
    ConfigResolveError,
    resolve_config,
    resolve_config_path,
)
from trailhead.pathint import create_shims, repo_root, trailhead_bin_executable
from trailhead.wire import LockError, WireError, default_manifest_paths, wire, wire_lock

_REPO_ROOT = repo_root()
_TRAILHEAD_BIN = _REPO_ROOT / "bin" / "trailhead"


def _resolve_cli_tools(cli_flags: dict[str, bool]) -> dict[str, Path]:
    """Resolve each enabled CLI-bearing tool to its shippable binary path.

    ``cli_flags`` (from ``ResolvedConfig``) already only contains tools whose
    manifest declares ``cli_bin`` (see ``install_config._resolve_cli_flags``);
    this resolves each enabled one via its manifest's ``plugin_root / cli_bin``
    and drops any whose binary doesn't actually exist on disk.
    """
    manifests = cli_bearing_manifests(default_manifest_paths())
    tools: dict[str, Path] = {}
    for name, enabled in cli_flags.items():
        if not enabled:
            continue
        manifest = manifests[name]
        bin_path = manifest.plugin_root / manifest.cli_bin
        if bin_path.exists():
            tools[name] = bin_path
    return tools


def run_lore_init(
    lore_bin: Path,
    *,
    env: dict[str, str],
    runner=None,
) -> tuple[int, str]:
    """Invoke ``lore init`` non-interactively and return ``(returncode, stderr)``.

    Harness-agnostic (Axiom 1): wires the lore bootstrap step the same way the
    rest of install shells out to a CLI — there is NO harness-specific branching
    here. ``lore init`` is itself non-interactive and idempotent, so this is safe
    to run on every install / re-install.

    The runner is injectable (Axiom 6) so tests never invoke the real
    ``lore init`` against the user's vault/state. The default captures output and
    does NOT raise on a non-zero exit — the caller decides how to surface it (a
    failed bootstrap must propagate as a non-zero install exit with the lore
    stderr, never be swallowed).
    """
    if runner is None:

        def runner(args, **kw):
            import subprocess

            return subprocess.run(args, **kw)

    proc = runner(
        [str(lore_bin), "init"],
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stderr or ""


def run_install(
    *,
    config_arg: str | None = None,
    harnesses: list[str] | None = None,
    plugins: list[str] | None = None,
    no_camp: bool = False,
    no_lore: bool = False,
    no_portage: bool = False,
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
            no_portage=no_portage,
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
    # Build the CLI shim dir (harness-independent, additive).
    # The shim dir's contents encode the selection; `shellenv` adds it to PATH.
    # ------------------------------------------------------------------
    cli_tools = _resolve_cli_tools(cfg.cli_flags)

    shim_dir = None
    shim_build_failed = False
    if cli_tools:
        try:
            shim_dir = create_shims(cli_tools, str(_REPO_ROOT), env=_env).shim_dir
        except Exception as exc:
            # M1: a shim-dir failure is a warning — wiring succeeded.
            shim_build_failed = True
            print(
                f"trailhead: could not build the CLI shim dir: {exc}\n"
                f"  (the plugins are installed; the CLIs just aren't shimmed)",
                file=sys.stderr,
            )

    # ------------------------------------------------------------------
    # Bootstrap the lore machine (vault + index + guardrail + agent rules).
    # `lore init` is non-interactive + idempotent; a failed bootstrap must
    # surface as a non-zero install exit with the lore stderr — never swallowed.
    # ------------------------------------------------------------------
    if "lore" in cli_tools:
        rc, lore_stderr = run_lore_init(cli_tools["lore"], env=_env, runner=runner)
        if rc != 0:
            if lore_stderr:
                print(lore_stderr.rstrip(), file=sys.stderr)
            print(
                f"trailhead: lore init failed (exit {rc})",
                file=sys.stderr,
            )
            return 1

    no_harness = not cfg.harnesses

    if as_json:
        _print_json_summary(
            cfg, wired, shim_dir, no_harness=no_harness, shim_build_failed=shim_build_failed
        )
    else:
        _print_human_summary(
            cfg,
            wired,
            shim_dir,
            no_harness=no_harness,
            cli_tools=cli_tools,
            shim_build_failed=shim_build_failed,
        )

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


def _collect_overrides(cfg) -> list[tuple[str, str, str, str, str]]:
    """Collect active file_path overrides as (harness, plugin, kind, name, file_path).

    A config can point a selectable subagent/skill NAME at an arbitrary
    ``file_path``; ``compose.py`` copies that content into the composed tree
    UNCONFINED (deliberately — see its docstring) and registers it as trusted
    agent content, re-materialized on every install. Overrides are otherwise
    invisible to whoever runs `trailhead install` — this collection feeds the
    summary sections below so an override is never silent.
    """
    out: list[tuple[str, str, str, str, str]] = []
    for rh in cfg.harnesses:
        for plugin in rh.plugins:
            for kind, selection in (("subagent", plugin.subagents), ("skill", plugin.skills)):
                for name, override in selection.items():
                    if override is not None:
                        out.append((rh.name, plugin.name, kind, name, override))
    return out


def _print_human_summary(
    cfg, wired, shim_dir, *, no_harness: bool, cli_tools: dict[str, Path], shim_build_failed: bool
) -> None:
    lines: list[str] = []

    if wired:
        lines.append("installed plugins:")
        for harness_name, plugin_names in wired.items():
            lines.append(f"  {harness_name}: {', '.join(plugin_names) or '(none)'}")
        lines.append("")

    overrides = _collect_overrides(cfg)
    if overrides:
        lines.append("config overrides (non-repo content installed as trusted agent content):")
        for harness_name, plugin_name, kind, name, file_path in overrides:
            lines.append(f"  {harness_name}: {plugin_name}/{kind} {name} -> {file_path}")
        lines.append("")

    clis = sorted(name for name, enabled in cfg.cli_flags.items() if enabled)
    trailhead_available = trailhead_bin_executable(_REPO_ROOT)

    # The eval line only puts the plugin CLIs on PATH when the shim dir was
    # actually built — a failed build, or no resolvable CLI binaries, means
    # they must not be named in the "on your PATH" promise.
    path_clis = clis if shim_dir is not None else []
    commands = list(path_clis)
    if trailhead_available:
        commands.append("trailhead")

    if clis or trailhead_available:
        if shim_dir is not None and clis:
            lines.append(f"CLIs ({', '.join(clis)}): shims in {shim_dir}")
        elif clis and shim_build_failed:
            lines.append(
                f"CLIs ({', '.join(clis)}): could not build the shim dir "
                "(see warning above) — use each CLI's full path for now:"
            )
            for name in clis:
                bin_path = cli_tools.get(name)
                if bin_path is not None:
                    lines.append(f"    {name}: {bin_path}")
        if commands:
            lines.append(
                f"  {', '.join(commands)} on your PATH: add this to your shell profile:"
            )
            lines.append(f'    eval "$({_TRAILHEAD_BIN} shellenv)"')
            lines.append("  then restart your shell (or re-eval it in the current one)")
        lines.append("")

    if not no_harness:
        lines.append("start a fresh Claude Code session to load the installed plugins")

    print("\n".join(lines).rstrip())


def _print_json_summary(
    cfg, wired, shim_dir, *, no_harness: bool, shim_build_failed: bool
) -> None:
    data = {
        "harnesses": wired,
        "cli_flags": dict(cfg.cli_flags),
        "shim_dir": str(shim_dir) if shim_dir else None,
        "shim_build_failed": shim_build_failed,
        "shellenv": f'eval "$({_TRAILHEAD_BIN} shellenv)"',
        "no_harness": no_harness,
    }
    overrides = _collect_overrides(cfg)
    if overrides:
        data["overrides"] = [
            {"harness": h, "plugin": p, "kind": k, "name": n, "file_path": fp}
            for h, p, k, n, fp in overrides
        ]
    print(json.dumps(data))
