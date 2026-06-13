"""trailhead management CLI.

Subcommands: install / update / doctor / config
Each subcommand body is a stub (exits 0, prints "not yet wired") for Slice 0.
Later slices replace the bodies.

A-9 hygiene:
  - errors → stderr, normal output → stdout
  - main() returns an int exit code
  - bare `trailhead` and `trailhead --help` print a curated grouped menu,
    never a raw argparse dump
  - no color output (NO_COLOR / --no-color honored by omission)
"""

import argparse
import sys

from trailhead import __version__
from trailhead.capabilities import ConfineError, ManifestError
from trailhead.compose import CollisionError, DestConfinementError, UnknownCapabilityError
from trailhead.config import ConfigError
from trailhead.config_cmd import run_config
from trailhead.doctor import run_doctor
from trailhead.fetch import FetchError
from trailhead.install import run_install
from trailhead.manifest import InstallManifestError
from trailhead.pathint import PathIntegrationError
from trailhead.paths import PathResolutionError
from trailhead.presets import PresetError
from trailhead.uninstall import run_uninstall
from trailhead.update import run_update
from trailhead.wire import LockError, WireError

# Named error family — maps to a clean 'trailhead: <message>' line (M5 / A-9).
_TRAILHEAD_ERRORS = (
    ConfigError,
    InstallManifestError,
    FetchError,
    WireError,
    PathIntegrationError,
    PathResolutionError,
    PresetError,
    LockError,
    ManifestError,
    ConfineError,
    UnknownCapabilityError,
    CollisionError,
    DestConfinementError,
)

_CURATED_HELP = """\
trailhead {version} — manage and compose lore, craft, and camp plugins.

Commands:
  install     Wire a preset of tools and capabilities into the Claude Code harness.
  uninstall   Remove all wired tools and trailhead's PATH integration (keeps your data).
  update      Re-wire to the latest pinned manifest versions from the configured source.
  doctor      Roll up health checks across all wired tools.
  config      Read and write trailhead configuration (registry, preset, capabilities).

Run `trailhead <command> --help` for details on each command.
"""


def _print_curated_help() -> None:
    print(_CURATED_HELP.format(version=__version__))


def _cmd_install(args: argparse.Namespace) -> int:
    return run_install(
        args.preset,
        quiet=args.quiet,
        as_json=args.json,
    )


def _cmd_uninstall(args: argparse.Namespace) -> int:
    return run_uninstall(
        quiet=args.quiet,
        as_json=args.json,
        assume_yes=args.yes,
    )


def _cmd_update(_args: argparse.Namespace) -> int:
    return run_update()


def _cmd_doctor(args: argparse.Namespace) -> int:
    result = run_doctor(as_json=getattr(args, "json", False))
    if getattr(args, "json", False):
        import json
        print(json.dumps(result.data))
    else:
        print(result.human_output)
    return result.exit_code


def _cmd_config(args: argparse.Namespace) -> int:
    return run_config(args.config_args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trailhead",
        description="trailhead — manage and compose lore, craft, and camp plugins.",
        add_help=True,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"trailhead {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    install_p = subparsers.add_parser(
        "install",
        help="Wire a preset of tools and capabilities into the Claude Code harness.",
    )
    install_p.add_argument(
        "--preset",
        metavar="PRESET",
        default=None,
        help="Preset to install: minimal, standard, or full. Default: standard (or prompts on TTY).",
    )
    install_p.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress progress lines; summary is still printed.",
    )
    install_p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print machine-readable JSON summary instead of human-readable output.",
    )

    uninstall_p = subparsers.add_parser(
        "uninstall",
        help="Remove all wired tools and trailhead's PATH integration (keeps your data).",
    )
    uninstall_p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        default=False,
        help="Skip the confirmation prompt.",
    )
    uninstall_p.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress progress lines; summary is still printed.",
    )
    uninstall_p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print machine-readable JSON summary instead of human-readable output.",
    )

    subparsers.add_parser(
        "update",
        help="Re-wire to the latest pinned manifest versions from the configured source.",
    )

    doctor_p = subparsers.add_parser(
        "doctor",
        help="Roll up health checks across all wired tools.",
    )
    doctor_p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print machine-readable JSON aggregate.",
    )

    config_p = subparsers.add_parser(
        "config",
        help="Read and write trailhead configuration (registry, preset, capabilities).",
    )
    config_p.add_argument(
        "config_args",
        nargs=argparse.REMAINDER,
        help="Config subcommand and arguments (e.g. registry, path_integration, capabilities).",
    )

    return parser


def main() -> int:
    """Entry point. Returns an int exit code."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        _print_curated_help()
        return 0

    dispatch = {
        "install": _cmd_install,
        "uninstall": _cmd_uninstall,
        "update": _cmd_update,
        "doctor": _cmd_doctor,
        "config": _cmd_config,
    }
    handler = dispatch.get(args.command)
    if handler is None:
        print(f"trailhead: unknown command {args.command!r}", file=sys.stderr)
        return 1

    # M5 / A-9: top-level error guard — named errors produce a clean
    # 'trailhead: <message>' line on stderr + nonzero exit; no raw tracebacks.
    try:
        return handler(args)
    except _TRAILHEAD_ERRORS as exc:
        print(f"trailhead: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"trailhead: unexpected error: {exc}", file=sys.stderr)
        return 1
