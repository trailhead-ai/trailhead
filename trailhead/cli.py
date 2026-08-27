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
from trailhead.outpost_lifecycle import OutpostLifecycleError, restart, start, status, stop
from trailhead.pathint import PathIntegrationError, shellenv_lines
from trailhead.paths import PathResolutionError
from trailhead.uninstall import run_uninstall
from trailhead.update import FRESHNESS_WINDOW_SECONDS, check_for_update, run_update_apply
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
    OutpostLifecycleError,
)

_CURATED_HELP = """\
trailhead {version} — install and manage the lore/camp/craft/portage/outpost plugins.

Commands:
  install     Install agent-plugins into your code harness(es) + the camp/lore CLIs.
  uninstall   Remove the entire trailhead install (all plugins + CLIs). Keeps your data.
  doctor      Report what trailhead has installed (read-only).
  update      Upgrade the install (or --check for a read-only freshness check).
  shellenv    Print shell env to put the camp/lore CLIs on PATH (brew-style).

Install is config-driven and non-interactive. By default it auto-detects your
harness (e.g. ~/.claude → claude_code) and installs every plugin. Override with:
  --harness <name>   target a harness explicitly (repeatable; e.g. claude_code)
  --plugin <name>    install only these plugins (repeatable; default: all)
  --no-camp/--no-lore/--no-portage  skip installing that CLI onto PATH
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
        no_portage=args.no_portage,
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


def _cmd_update(args: argparse.Namespace) -> int:
    if not args.check:
        return run_update_apply(
            assume_yes=args.yes,
            dry_run=args.dry_run,
            timeout=args.timeout,
        )

    result = check_for_update(timeout=args.timeout, window=args.window)

    if args.json:
        import json

        print(json.dumps(result))
        return 0

    outcome = result["outcome"]
    sha = result["installed_sha"]
    short_sha = sha[:8] if sha else "unknown"
    if outcome == "ok":
        print(f"trailhead: up to date (installed {short_sha})")
    elif outcome == "behind":
        gaps = []
        if result["install_commits_behind"]:
            gaps.append(
                f"install is {result['install_commits_behind']} commit(s) behind the checkout"
            )
        if result["commits_behind"]:
            gaps.append(
                f"checkout is {result['commits_behind']} commit(s) behind its tracked branch"
            )
        print(f"trailhead: {'; '.join(gaps)} (installed {short_sha})")
    else:
        print(f"trailhead: update check inconclusive: {result['reason']}")
    return 0


def _cmd_shellenv(args: argparse.Namespace) -> int:
    # Print only — meant to be wrapped in `eval "$(trailhead shellenv)"`.
    sys.stdout.write(shellenv_lines(shell=args.shell))
    return 0


def _cmd_outpost(args: argparse.Namespace) -> int:
    dispatch = {"start": start, "stop": stop, "status": status, "restart": restart}
    handler = dispatch.get(args.outpost_command)
    if handler is None:
        # No subcommand given — print the group's help and signal misuse.
        args.outpost_parser.print_help(file=sys.stderr)
        return 2
    return handler()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trailhead",
        description="trailhead — install and manage the lore/camp/craft/portage/outpost plugins.",
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
        "--no-portage",
        action="store_true",
        default=False,
        help="Skip installing/updating the portage CLI onto PATH.",
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

    update_p = subparsers.add_parser(
        "update",
        help="Upgrade the install (or check with --check) against its source checkout's remote.",
    )
    update_p.add_argument(
        "--check",
        action="store_true",
        default=False,
        help="Run the read-only freshness check instead of upgrading.",
    )
    update_p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        default=False,
        help="Skip the interactive confirmation prompt (apply mode only).",
    )
    update_p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what an upgrade would do without changing anything (apply mode only).",
    )
    update_p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print a machine-readable JSON result.",
    )
    update_p.add_argument(
        "--timeout",
        type=int,
        default=10,
        metavar="SECONDS",
        help="Abandon the git fetch after this many seconds (default: 10).",
    )
    update_p.add_argument(
        "--window",
        type=int,
        default=FRESHNESS_WINDOW_SECONDS,
        metavar="SECONDS",
        help="Freshness window between network fetches (default: 86400, 24h).",
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

    outpost_p = subparsers.add_parser(
        "outpost",
        help="Manage the outpost daemon (start | stop | status | restart).",
    )
    outpost_sub = outpost_p.add_subparsers(dest="outpost_command", metavar="<verb>")
    outpost_sub.add_parser("start", help="Spawn the outpost daemon detached (idempotent).")
    outpost_sub.add_parser("stop", help="Stop the outpost daemon and remove its pidfile.")
    outpost_sub.add_parser("status", help="Report daemon liveness + /health (structured exit codes).")
    outpost_sub.add_parser(
        "restart", help="Rebuild the outpost checkout, then stop and start the daemon."
    )
    # Carry the parser so the handler can print help when no verb is given.
    outpost_p.set_defaults(outpost_parser=outpost_p)

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
        "update": _cmd_update,
        "shellenv": _cmd_shellenv,
        "outpost": _cmd_outpost,
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
