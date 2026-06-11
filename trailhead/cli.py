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
from trailhead.install import run_install

_CURATED_HELP = """\
trailhead {version} — manage and compose lore, forge, and camp plugins.

Commands:
  install   Wire a preset of tools and capabilities into the Claude Code harness.
  update    Re-wire to the latest pinned manifest versions from the configured source.
  doctor    Roll up health checks across all wired tools.
  config    Read and write trailhead configuration (registry, preset, capabilities).

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


def _cmd_update(_args: argparse.Namespace) -> int:
    print("update: not yet wired (Slice 5)")
    return 0


def _cmd_doctor(_args: argparse.Namespace) -> int:
    print("doctor: not yet wired (Slice 5)")
    return 0


def _cmd_config(_args: argparse.Namespace) -> int:
    print("config: not yet wired (Slice 5)")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trailhead",
        description="trailhead — manage and compose lore, forge, and camp plugins.",
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

    subparsers.add_parser(
        "update",
        help="Re-wire to the latest pinned manifest versions from the configured source.",
    )
    subparsers.add_parser(
        "doctor",
        help="Roll up health checks across all wired tools.",
    )
    subparsers.add_parser(
        "config",
        help="Read and write trailhead configuration (registry, preset, capabilities).",
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
        "update": _cmd_update,
        "doctor": _cmd_doctor,
        "config": _cmd_config,
    }
    handler = dispatch.get(args.command)
    if handler is None:
        print(f"trailhead: unknown command {args.command!r}", file=sys.stderr)
        return 1

    return handler(args)
