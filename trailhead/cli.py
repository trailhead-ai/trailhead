"""trailhead management CLI.

Subcommands: install / uninstall / doctor / shellenv

Everything is CLI/config-driven and non-interactive so an agent can run it.

Output hygiene:
  - errors → stderr, normal output → stdout
  - main() returns an int exit code
  - bare `trailhead` and `trailhead --help` print a curated grouped menu
  - no color output
"""

import argparse
import sys

from trailhead import __version__
from trailhead.capabilities import ConfineError, ManifestError
from trailhead.compose import (
    CollisionError,
    DestConfinementError,
    OverrideError,
    UnknownSkillError,
    UnknownSubagentError,
)
from trailhead.doctor import run_doctor
from trailhead.harness import HarnessError
from trailhead.install import run_install
from trailhead.install_config import ConfigResolveError
from trailhead.pathint import PathIntegrationError, shellenv_lines
from trailhead.paths import PathResolutionError
from trailhead.uninstall import run_uninstall
from trailhead.wire import LockError, WireError

# Named error family — maps to a clean 'trailhead: <message>' line.
_TRAILHEAD_ERRORS = (
    ConfigResolveError,
    ManifestError,
    ConfineError,
    UnknownSubagentError,
    UnknownSkillError,
    OverrideError,
    CollisionError,
    DestConfinementError,
    HarnessError,
    WireError,
    LockError,
    PathIntegrationError,
    PathResolutionError,
)

_CURATED_HELP = """\
trailhead {version} — install and manage the lore/camp/craft/portage plugins.

Commands:
  install     Install agent-plugins into your code harness(es) + the camp/lore CLIs.
  uninstall   Remove the entire trailhead install (all plugins + CLIs). Keeps your data.
  doctor      Report what trailhead has installed (read-only).
  shellenv    Print shell env to put the camp/lore CLIs on PATH (brew-style).

Install is config-driven and non-interactive. By default it auto-detects your
harness (e.g. ~/.claude → claude_code) and installs every plugin. Override with:
  --harness <name>   target a harness explicitly (repeatable; e.g. claude_code)
  --plugin <name>    install only these plugins (repeatable; default: all)
  --no-camp/--no-lore  skip installing that CLI onto PATH
  --config <path>    drive the install from a TOML config (see config/default.toml)

Run `trailhead <command> --help` for details on each command.
"""


def _print_curated_help() -> None:
    print(_CURATED_HELP.format(version=__version__))


def _cmd_install(args: argparse.Namespace) -> int:
    return run_install(
        config_arg=args.config,
        harnesses=args.harness or None,
        plugins=args.plugin or None,
        no_camp=args.no_camp,
        no_lore=args.no_lore,
        quiet=args.quiet,
        as_json=args.json,
    )


def _cmd_uninstall(args: argparse.Namespace) -> int:
    return run_uninstall(
        quiet=args.quiet,
        as_json=args.json,
        assume_yes=args.yes,
    )


def _cmd_doctor(args: argparse.Namespace) -> int:
    result = run_doctor(as_json=args.json)
    if args.json:
        import json

        print(json.dumps(result.data))
    else:
        print(result.human_output)
    return result.exit_code


def _cmd_shellenv(args: argparse.Namespace) -> int:
    # Print only — meant to be wrapped in `eval "$(trailhead shellenv)"`.
    sys.stdout.write(shellenv_lines(shell=args.shell))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trailhead",
        description="trailhead — install and manage the lore/camp/craft/portage plugins.",
        add_help=True,
    )
    parser.add_argument("--version", action="version", version=f"trailhead {__version__}")

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    install_p = subparsers.add_parser(
        "install",
        help="Install agent-plugins into your code harness(es) + the camp/lore CLIs.",
    )
    install_p.add_argument(
        "--harness",
        action="append",
        metavar="NAME",
        default=[],
        help="Target harness (repeatable; e.g. claude_code). Default: auto-detect.",
    )
    install_p.add_argument(
        "--plugin",
        action="append",
        metavar="NAME",
        default=[],
        help="Install only these agent-plugins (repeatable). Default: all.",
    )
    install_p.add_argument(
        "--no-camp",
        action="store_true",
        default=False,
        help="Skip installing/updating the camp CLI onto PATH.",
    )
    install_p.add_argument(
        "--no-lore",
        action="store_true",
        default=False,
        help="Skip installing/updating the lore CLI onto PATH.",
    )
    install_p.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="Config TOML (absolute, or relative to the repo config/ dir). "
        "Default: config/default.toml.",
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
        help="Print a machine-readable JSON summary.",
    )

    uninstall_p = subparsers.add_parser(
        "uninstall",
        help="Remove the entire trailhead install (all plugins + CLIs). Keeps your data.",
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
        help="Print a machine-readable JSON summary.",
    )

    doctor_p = subparsers.add_parser(
        "doctor", help="Report what trailhead has installed (read-only)."
    )
    doctor_p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print a machine-readable JSON report.",
    )

    shellenv_p = subparsers.add_parser(
        "shellenv",
        help="Print shell env to put the camp/lore CLIs on PATH (brew-style).",
    )
    shellenv_p.add_argument(
        "--shell",
        choices=["fish", "zsh", "bash"],
        default=None,
        help="Target shell. Default: detect from $SHELL.",
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
        "doctor": _cmd_doctor,
        "shellenv": _cmd_shellenv,
    }
    handler = dispatch.get(args.command)
    if handler is None:
        print(f"trailhead: unknown command {args.command!r}", file=sys.stderr)
        return 1

    # Top-level error guard — named errors produce a clean
    # 'trailhead: <message>' line on stderr + nonzero exit; no raw tracebacks.
    try:
        return handler(args)
    except _TRAILHEAD_ERRORS as exc:
        print(f"trailhead: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"trailhead: unexpected error: {exc}", file=sys.stderr)
        return 1
