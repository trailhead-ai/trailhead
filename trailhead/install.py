"""Install orchestrator for `trailhead install`.

This module wires the pipeline:
  1. Preset selection (--preset / interactive A-6 menu / non-TTY default)
  2. Resolve preset → {tool: set[cap]} via presets.resolve
  3. Verify the repo in place (already-present-repo case, A-7)
  4. Wire the selection (wire.wire)
  5. Persist config (config.save_config)
  6. PATH integration (pathint.install_path_integration)
  7. Print the summary (A-1, A-2, A-3, A-7, A-10)

A-9 hygiene:
  - progress/summary → stdout
  - errors → stderr
  - nonzero exit on failure
  - NO_COLOR / --no-color honored (no ANSI used anywhere)
  - --json for machine-readable output
  - --quiet to suppress progress lines

Hermeticity (B-3):
  The real wire() and install_path_integration() are imported at module level so
  tests can patch them via patch("trailhead.install.wire") and
  patch("trailhead.install.install_path_integration").

  _is_tty() is a thin wrapper around sys.stdin.isatty() so tests can patch it
  via patch("trailhead.install._is_tty", return_value=...).

U-1 / A-7: for the already-present-repo case (dogfood) we verify-in-place and
  print "verified in place (no download needed)".  The fresh-clone path is not
  reached in the current dogfood scenario but the error handling covers it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from trailhead.config import TrailheadConfig, load_config, save_config
from trailhead.fetch import FetchError, verify_present_repo
from trailhead.manifest import InstallManifest, load_install_manifest
from trailhead.pathint import PathIntegrationResult, install_path_integration
from trailhead.paths import config_dir
from trailhead.presets import PresetError, resolve
from trailhead.wire import WireError, wire

# ---------------------------------------------------------------------------
# Injected-for-tests helpers
# ---------------------------------------------------------------------------

_MANIFEST_PATH = Path(__file__).parent / "install_manifest.toml"
_REPO_ROOT = Path(__file__).parent.parent

_A6_MENU = """\
Preset? [minimal / standard / full] (default: standard)
  minimal:  lore only — capture + recall (lowest buy-in)
  standard: lore + camp + forge subset (the common loop)
  full:     everything
"""


def _is_tty() -> bool:
    """Return True if stdin is interactive. Thin wrapper so tests can patch it."""
    return sys.stdin.isatty()


# ---------------------------------------------------------------------------
# Public API: run_install
# ---------------------------------------------------------------------------


def run_install(
    preset_arg: str | None,
    *,
    env: dict[str, str] | None = None,
    quiet: bool = False,
    as_json: bool = False,
) -> int:
    """Execute the install pipeline. Returns an int exit code.

    Args:
        preset_arg:   The --preset value, or None for interactive/default.
        env:          Env dict for path resolution (hermeticity).
        quiet:        Suppress progress lines (summary still printed).
        as_json:      Print machine-readable JSON instead of human summary.

    Returns:
        0 on success, nonzero on failure.
    """
    _env = env if env is not None else {}
    is_tty = _is_tty()

    # ------------------------------------------------------------------
    # Step 1: Resolve the preset name
    # ------------------------------------------------------------------
    preset_name = _resolve_preset_name(preset_arg, is_tty=is_tty, quiet=quiet)
    if preset_name is None:
        return 1

    # ------------------------------------------------------------------
    # Step 2: Resolve preset → {tool: set[cap]}
    # ------------------------------------------------------------------
    try:
        selection = resolve(preset_name)
    except PresetError as exc:
        print(f"trailhead: {exc}", file=sys.stderr)
        return 1

    # ------------------------------------------------------------------
    # Step 3: Load install manifest + verify repo in place (A-7)
    # ------------------------------------------------------------------
    cfg = load_config(env=_env)
    try:
        manifest = load_install_manifest(
            _MANIFEST_PATH,
            cfg.registry,
            local_root=_REPO_ROOT,
        )
    except Exception as exc:
        print(f"trailhead: failed to load install manifest: {exc}", file=sys.stderr)
        return 1

    # Verify the repo entry (already-present-repo case)
    trailhead_entry = _find_trailhead_entry(manifest)
    verify_msg = ""
    if trailhead_entry is not None:
        if not quiet and not as_json:
            sha_short = trailhead_entry.rev[:8]
            print(f"verifying trailhead@{sha_short}…")
        try:
            verify_present_repo(trailhead_entry, repo_path=_REPO_ROOT)
            verify_msg = "verified in place (no download needed)"
        except FetchError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    else:
        verify_msg = "verified in place (no download needed)"

    # ------------------------------------------------------------------
    # Step 4: Wire the selection
    # ------------------------------------------------------------------
    if not quiet and not as_json:
        for tool, caps in selection.items():
            caps_str = ", ".join(sorted(caps)) if caps else "base"
            print(f"wiring {tool} ({caps_str})…")

    try:
        wire(selection, env=_env)
    except (WireError, FetchError, Exception) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # ------------------------------------------------------------------
    # Step 5: Persist config (R-2: only after successful wire)
    # ------------------------------------------------------------------
    capabilities_dict: dict[str, list[str]] = {
        tool: sorted(caps) for tool, caps in selection.items()
    }
    cfg.preset = preset_name
    cfg.capabilities = capabilities_dict
    save_config(cfg, env=_env)

    # ------------------------------------------------------------------
    # Step 6: PATH integration
    # ------------------------------------------------------------------
    trailhead_root = str(_REPO_ROOT)
    wired_tool_bins: dict[str, Path] = {}
    for tool in selection:
        bin_path = _REPO_ROOT / "tools" / tool / "plugins" / tool / "bin" / tool
        if bin_path.exists():
            wired_tool_bins[tool] = bin_path

    pathint_result: PathIntegrationResult | None = None
    if cfg.path_integration:
        try:
            pathint_result = install_path_integration(
                wired_tool_bins,
                trailhead_root,
                is_tty=is_tty,
                env=_env,
            )
        except Exception as exc:
            print(f"trailhead: PATH integration failed: {exc}", file=sys.stderr)
            return 1

    # ------------------------------------------------------------------
    # Step 7: Print the summary
    # ------------------------------------------------------------------
    config_path = config_dir("trailhead", env=_env) / "config.toml"

    if as_json:
        _print_json_summary(
            preset_name=preset_name,
            selection=selection,
            config_path=config_path,
            pathint_result=pathint_result,
        )
    else:
        _print_human_summary(
            preset_name=preset_name,
            selection=selection,
            verify_msg=verify_msg,
            config_path=config_path,
            pathint_result=pathint_result,
            quiet=quiet,
        )

    return 0


# ---------------------------------------------------------------------------
# Internal: preset name resolution
# ---------------------------------------------------------------------------


def _resolve_preset_name(
    preset_arg: str | None,
    *,
    is_tty: bool,
    quiet: bool,
) -> str | None:
    """Resolve the preset name from --preset, interactive prompt, or non-TTY default.

    Returns the resolved preset name, or None on invalid input.
    """
    if preset_arg is not None:
        # Validate explicitly: PresetError will be raised by resolve() later,
        # but we want to fail fast for unknown names here too.
        try:
            resolve(preset_arg)
        except PresetError as exc:
            print(f"trailhead: {exc}", file=sys.stderr)
            return None
        return preset_arg

    if is_tty:
        return _interactive_preset_prompt()
    else:
        # A-8 / non-TTY default: never block on stdin
        msg = "defaulting to standard preset (non-interactive)"
        if not quiet:
            print(msg)
        return "standard"


def _interactive_preset_prompt() -> str:
    """Show the A-6 self-guiding preset menu and read user input.

    Bare enter → "standard" (the default).
    """
    print(_A6_MENU, end="")
    try:
        raw = sys.stdin.readline()
    except (EOFError, KeyboardInterrupt):
        return "standard"
    choice = raw.strip().lower()
    if not choice:
        return "standard"
    return choice


# ---------------------------------------------------------------------------
# Internal: manifest helpers
# ---------------------------------------------------------------------------


def _find_trailhead_entry(manifest: InstallManifest):
    """Return the 'trailhead' RepoEntry from the manifest, or None."""
    for entry in manifest.repos:
        if entry.name == "trailhead":
            return entry
    return None


# ---------------------------------------------------------------------------
# Internal: summary printing (A-1, A-2, A-3, A-7, A-10)
# ---------------------------------------------------------------------------


def _print_human_summary(
    *,
    preset_name: str,
    selection: dict[str, set[str]],
    verify_msg: str,
    config_path: Path,
    pathint_result: PathIntegrationResult | None,
    quiet: bool,
) -> None:
    """Print the A-10 multi-line grouped install summary."""
    lines = []

    # A-7: honest source line
    lines.append(f"  {verify_msg}")
    lines.append("")

    # A-10: wired tools grouped
    lines.append("wired:")
    for tool, caps in sorted(selection.items()):
        caps_str = ", ".join(sorted(caps)) if caps else "base"
        lines.append(f"  {tool} ({caps_str})")
    lines.append("")

    # A-3: PATH integration line
    if pathint_result is not None:
        if pathint_result.skip_message:
            lines.append(f"PATH: {pathint_result.skip_message}")
        elif pathint_result.rc_path is not None:
            lines.append(
                f"PATH: added a shim dir to {pathint_result.rc_path} — "
                f"remove with `trailhead config path_integration off`"
            )
        lines.append("")

    # Config path
    lines.append(f"config: {config_path}")
    lines.append("")

    # U-1 residual: session restart note
    lines.append("start a fresh Claude Code session to load the wired tools")
    lines.append("")

    # A-1: next step
    lines.append(
        'next: run `lore capture "my first note"`, then start a Claude Code '
        "session and `/lore:recall` to retrieve it"
    )

    print("\n".join(lines))


def _print_json_summary(
    *,
    preset_name: str,
    selection: dict[str, set[str]],
    config_path: Path,
    pathint_result: PathIntegrationResult | None,
) -> None:
    """Print the --json machine-readable summary (A-9)."""
    wired = {tool: sorted(caps) for tool, caps in selection.items()}
    shim_dir = str(pathint_result.shim_dir) if pathint_result else None
    rc_path = str(pathint_result.rc_path) if pathint_result and pathint_result.rc_path else None

    data = {
        "preset": preset_name,
        "wired": wired,
        "config_path": str(config_path),
        "shim_dir": shim_dir,
        "rc_path": rc_path,
    }
    print(json.dumps(data))
