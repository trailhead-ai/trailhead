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
import json
import os
import sys
from pathlib import Path

from trailhead import __version__, broker_header
from trailhead.capabilities import ConfineError, ManifestError
from trailhead.compose import (
    CollisionError,
    DestConfinementError,
    OverrideError,
    UnknownSkillError,
    UnknownSubagentError,
)
from trailhead.core_vault import core_show, resolve_lore_bin
from trailhead.doctor import run_doctor
from trailhead.harness import HarnessError, detect_harnesses, get_harness
from trailhead.install import run_install
from trailhead.install_config import ConfigResolveError
from trailhead.launch_profile import (
    BROKER_SCOPE,
    BROKER_SERVER,
    DRIVE_GROUPS_OUTPUT_KEYS,
    HUB_PROFILE,
    RITUAL_PROFILE,
    RITUAL_ROLE,
    ROLE_WORKSPACE,
    LaunchProfileError,
    broker_launch,
    broker_mcp_config_path,
    hub_home,
    resolve_profile,
)
from trailhead.outpost_lifecycle import (
    OutpostLifecycleError,
    open_ui,
    restart,
    start,
    status,
    stop,
)
from trailhead.outpost_supervisor import disable as supervisor_disable
from trailhead.outpost_supervisor import enable as supervisor_enable
from trailhead.pathint import PathIntegrationError, shellenv_lines
from trailhead.paths import PathResolutionError
from trailhead.ranger import read_outcome
from trailhead.ranger.config import DEFAULT_LAUNCH_ACCOUNT, HubConfig, HubConfigError, read_hub_config
from trailhead.ranger.drive_groups import DriveGroupsError, drive_groups
from trailhead.uninstall import run_uninstall
from trailhead.update import (
    FRESHNESS_WINDOW_SECONDS,
    SCHEMA_VERSION as UPDATE_SCHEMA_VERSION,
    automatic_check_disabled,
    check_for_update,
    run_update_apply,
)
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
    HubConfigError,
    LaunchProfileError,
    DriveGroupsError,
)

_CURATED_HELP = """\
trailhead {version} — install and manage the lore/camp/craft/portage/outpost plugins.

Commands:
  install     Install agent-plugins into your code harness(es) + the camp/lore CLIs.
  uninstall   Remove the entire trailhead install (all plugins + CLIs). Keeps your data.
  doctor      Report what trailhead has installed (read-only).
  update      Upgrade the install (or --check for a read-only freshness check).
  shellenv    Print shell env to put the camp/lore CLIs on PATH (brew-style).
  core show   Print a record from trailhead's own core vault (`core show <kind>/<name>`).

Install is config-driven and non-interactive. By default it auto-detects your
harness (e.g. ~/.claude → claude_code) and installs every plugin. Override with:
  --harness <name>   target a harness explicitly (repeatable; e.g. claude_code)
  --plugin <name>    install only these plugins (repeatable; default: all)
  --no-camp/--no-lore/--no-portage/--no-docket  skip installing that CLI onto PATH
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
        no_docket=args.no_docket,
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
    if args.automatic and not args.check:
        print("trailhead: --automatic requires --check", file=sys.stderr)
        return 1

    if not args.check:
        return run_update_apply(
            assume_yes=args.yes,
            dry_run=args.dry_run,
            timeout=args.timeout,
        )

    if args.automatic and automatic_check_disabled():
        if args.json:
            import json

            print(json.dumps({"schema_version": UPDATE_SCHEMA_VERSION, "automatic_check": "off"}))
        else:
            print("trailhead: automatic update checks are off")
        return 0

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

    outpost = result.get("outpost")
    if outpost is not None:
        if outpost["outcome"] == "ok":
            print("outpost: up to date")
        elif outpost["outcome"] == "behind":
            print(
                f"outpost: checkout is {outpost['commits_behind']} commit(s) behind "
                "its tracked branch"
            )
        else:
            print(f"outpost: update check inconclusive: {outpost['reason']}")
    return 0


def _cmd_shellenv(args: argparse.Namespace) -> int:
    # Print only — meant to be wrapped in `eval "$(trailhead shellenv)"`.
    sys.stdout.write(shellenv_lines(shell=args.shell))
    return 0


def _cmd_outpost(args: argparse.Namespace) -> int:
    dispatch = {
        "start": start,
        "stop": stop,
        "status": status,
        "restart": restart,
        "open": open_ui,
        "enable": supervisor_enable,
        "disable": supervisor_disable,
    }
    handler = dispatch.get(args.outpost_command)
    if handler is None:
        # No subcommand given — print the group's help and signal misuse.
        args.outpost_parser.print_help(file=sys.stderr)
        return 2
    return handler()


def _cmd_core(args: argparse.Namespace) -> int:
    if args.core_command != "show":
        args.core_parser.print_help(file=sys.stderr)
        return 2
    result = core_show(args.record_id, env=dict(os.environ), lore_bin=resolve_lore_bin())
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.code


def _cmd_broker_header(args: argparse.Namespace) -> int:
    code, text = broker_header.run(args.credential_file)
    print(text, file=sys.stderr if code else sys.stdout)
    return code


def _cmd_gateway(args: argparse.Namespace) -> int:
    from trailhead.gateway.register import run as register_run

    return register_run(["--hook"] if args.hook else [])


def _launch_account(config: HubConfig) -> str | None:
    """The launch account an account's sessions bind; ``None`` for the harness's default one."""
    return None if config.launch_account == DEFAULT_LAUNCH_ACCOUNT else config.launch_account


def _cmd_session_start(args: argparse.Namespace) -> int:
    if not args.json:
        print("trailhead: session-start prints JSON; pass --json", file=sys.stderr)
        return 1
    ritual = args.role == RITUAL_ROLE
    if args.setup_sandbox_only:
        return _session_start_setup_sandbox_only(args, ritual)
    if ritual and args.broker_session is not None:
        print(
            "trailhead: --broker-session is not accepted for the ritual role; "
            "a ritual session has no broker",
            file=sys.stderr,
        )
        return 1
    if ritual != (args.workspace is not None):
        print(
            "trailhead: --workspace is required for the ritual role and refused for the hub"
            if ritual
            else "trailhead: --workspace belongs to the ritual role; a hub starts in its own home",
            file=sys.stderr,
        )
        return 1
    if ritual != (args.session is not None):
        print(
            "trailhead: --session is required for the ritual role and refused for the hub"
            if ritual
            else "trailhead: --session belongs to the ritual role; a hub names its session with --broker-session",
            file=sys.stderr,
        )
        return 1
    if not ritual and args.vault is not None:
        print(
            "trailhead: --vault belongs to the ritual role; a hub writes every vault of its account",
            file=sys.stderr,
        )
        return 1
    env = dict(os.environ)
    launch = broker_launch(args.broker_session, env=env) if args.broker_session is not None else None
    session_dir = broker_mcp_config_path(args.session, env=env).parent if ritual else None
    config = read_hub_config(args.account, env=env)
    if ritual:
        profile = resolve_profile(
            RITUAL_PROFILE, args.account, env=env, workspace=args.workspace, vault=args.vault
        )
        directory = profile.paths[ROLE_WORKSPACE]
    else:
        profile = resolve_profile(HUB_PROFILE, args.account, env=env)
        directory = hub_home(args.account, env=env)
    prompt = None
    if args.prompt_file:
        try:
            prompt = Path(args.prompt_file).read_text()
        except (OSError, UnicodeDecodeError) as exc:
            print(f"trailhead: cannot read --prompt-file {args.prompt_file}: {exc}", file=sys.stderr)
            return 1
    harnesses = [get_harness(args.harness)] if args.harness else detect_harnesses(env)
    if not harnesses:
        print("trailhead: no harness detected; pass --harness", file=sys.stderr)
        return 1
    start = harnesses[0].session_start(
        profile,
        directory,
        prompt,
        interactive=not args.headless,
        account=_launch_account(config),
        env=env,
        broker=launch,
        session_dir=session_dir,
    )
    if start is None:
        print(
            f"trailhead: harness {harnesses[0].name!r} cannot start a confined session; refusing",
            file=sys.stderr,
        )
        return 1
    output = {
        "argv": start.argv,
        "env_set": start.env_set,
        "env_unset": start.env_unset,
        "directory": str(directory),
    }
    files = [{"path": f.path, "content": f.content, "mode": f.mode} for f in start.files]
    if ritual:
        output["files"] = files
        output["setup_sandbox"] = start.setup_sandbox
    if launch is not None:
        output["broker"] = {
            "server": BROKER_SERVER,
            "scope": BROKER_SCOPE,
            "credential_path": launch.credential_path,
        }
        output["files"] = files
    print(json.dumps(output))
    return 0


def _session_start_setup_sandbox_only(args: argparse.Namespace, ritual: bool) -> int:
    refused = (args.workspace, args.prompt_file, args.broker_session, args.session, args.vault)
    if not ritual or any(value is not None for value in refused):
        print(
            "trailhead: --setup-sandbox-only belongs to the ritual role alone and takes no "
            "--workspace, --prompt-file, --broker-session, --session or --vault",
            file=sys.stderr,
        )
        return 1
    env = dict(os.environ)
    config = read_hub_config(args.account, env=env)
    profile = resolve_profile(RITUAL_PROFILE, args.account, env=env, without_workspace=True)
    harnesses = [get_harness(args.harness)] if args.harness else detect_harnesses(env)
    if not harnesses:
        print("trailhead: no harness detected; pass --harness", file=sys.stderr)
        return 1
    section = harnesses[0].setup_sandbox(profile, account=_launch_account(config), env=env)
    if section is None:
        print(
            f"trailhead: harness {harnesses[0].name!r} cannot derive a setup sandbox; refusing",
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"setup_sandbox": section}))
    return 0


def _cmd_drive_groups(args: argparse.Namespace) -> int:
    if not args.json:
        print("trailhead: drive-groups prints JSON; pass --json", file=sys.stderr)
        return 1
    groups = drive_groups(args.account, args.vault, env=dict(os.environ))
    print(json.dumps({DRIVE_GROUPS_OUTPUT_KEYS[0]: groups}))
    return 0


def _cmd_read_outcome(args: argparse.Namespace) -> int:
    for ok, problem in (
        (args.json, "prints JSON; pass --json"),
        (read_outcome.valid_spec(args.spec), "--spec is not a spec name"),
        (read_outcome.valid_vault(args.vault), "--vault is not a safe vault name"),
    ):
        if not ok:
            print(f"trailhead: read-outcome: {problem}", file=sys.stderr)
            return 2
    env = dict(os.environ)
    verdict = read_outcome.judge(
        args.spec, args.vault, Path(args.output_file), show=read_outcome.lore_show(env)
    )
    print(json.dumps(verdict))
    return 0


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
        "--no-docket",
        action="store_true",
        default=False,
        help="Skip installing/updating the docket CLI onto PATH.",
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
        help=(
            "Upgrade the install and a configured outpost checkout (or check with "
            "--check) against their tracked branches."
        ),
    )
    update_p.add_argument(
        "--check",
        action="store_true",
        default=False,
        help="Run the read-only freshness check instead of upgrading.",
    )
    update_p.add_argument(
        "--automatic",
        action="store_true",
        default=False,
        help=(
            "Mark the check as unattended (requires --check): honour the update-check "
            "opt-out and print only an off marker when it is set."
        ),
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
        help="Manage the outpost daemon (start | stop | status | restart | open).",
    )
    outpost_sub = outpost_p.add_subparsers(dest="outpost_command", metavar="<verb>")
    outpost_sub.add_parser("start", help="Spawn the outpost daemon detached (idempotent).")
    outpost_sub.add_parser("stop", help="Stop the outpost daemon and remove its pidfile.")
    outpost_sub.add_parser("status", help="Report daemon liveness + /health (structured exit codes).")
    outpost_sub.add_parser(
        "restart", help="Rebuild the outpost checkout, then stop and start the daemon."
    )
    outpost_sub.add_parser("open", help="Open the outpost web UI in your browser.")
    outpost_sub.add_parser(
        "enable", help="Register the outpost daemon with the host supervisor (launchd/systemd)."
    )
    outpost_sub.add_parser(
        "disable", help="Deregister and remove the outpost daemon's host-supervisor entry."
    )
    # Carry the parser so the handler can print help when no verb is given.
    outpost_p.set_defaults(outpost_parser=outpost_p)

    gateway_p = subparsers.add_parser(
        "gateway",
        help="Outpost gateway helpers (register).",
    )
    gateway_sub = gateway_p.add_subparsers(dest="gateway_command", metavar="<verb>", required=True)
    register_p = gateway_sub.add_parser(
        "register", help="Register this session with Outpost's gateway (the SessionStart hook runs the same step)."
    )
    register_p.add_argument(
        "--hook", action="store_true", help="Run as a hook: silent on success, always exit 0."
    )

    core_p = subparsers.add_parser(
        "core",
        help="Read records from trailhead's own core vault (show <kind>/<name>).",
    )
    core_sub = core_p.add_subparsers(dest="core_command", metavar="<verb>")
    core_show_p = core_sub.add_parser(
        "show",
        help="Print a core-vault record; exit 0 ok, 1 lore failed, 2 bad id, 3 core vault unavailable.",
    )
    core_show_p.add_argument("record_id", metavar="<kind>/<name>", help="The record to read from core.")
    core_p.set_defaults(core_parser=core_p)

    session_p = subparsers.add_parser(
        "session-start",
        help="Print the argv and env to start a confined session for a role (never execs).",
    )
    session_p.add_argument("--role", required=True, choices=["hub", RITUAL_ROLE], help="Role to start.")
    session_p.add_argument(
        "--workspace", metavar="W", default=None, help="Camp workspace directory (ritual role only)."
    )
    session_p.add_argument("--account", required=True, help="Account short name.")
    session_p.add_argument("--prompt-file", metavar="P", default=None, help="File holding the first prompt.")
    session_p.add_argument(
        "--headless", action="store_true", default=False, help="Start a non-interactive session."
    )
    session_p.add_argument("--harness", metavar="NAME", default=None, help="Default: first detected.")
    session_p.add_argument(
        "--broker-session",
        metavar="ID",
        default=None,
        help="Outpost's session id; adds a broker section naming where its credentials live.",
    )
    session_p.add_argument(
        "--session",
        metavar="ID",
        default=None,
        help="Outpost's session id for a ritual; its session files land in that session's directory.",
    )
    session_p.add_argument(
        "--vault",
        metavar="NAME",
        default=None,
        help=(
            "The one lore vault a ritual may write records in (required for the ritual role); "
            "every other vault is write-denied."
        ),
    )
    session_p.add_argument(
        "--setup-sandbox-only",
        action="store_true",
        default=False,
        help="Print only the ritual's setup_sandbox section; takes no workspace or prompt.",
    )
    session_p.add_argument("--json", action="store_true", default=False, help="Print JSON (required).")

    drive_p = subparsers.add_parser(
        "drive-groups",
        help="Print the camp groups on an account's launch account that bind a vault (JSON).",
    )
    drive_p.add_argument("--account", required=True, help="Account short name.")
    drive_p.add_argument("--vault", required=True, help="Vault name the group must bind.")
    drive_p.add_argument("--json", action="store_true", default=False, help="Print JSON (required).")

    outcome_p = subparsers.add_parser(
        "read-outcome",
        help="Judge a ritual session's captured output against the record it names (JSON verdict).",
    )
    outcome_p.add_argument("--spec", required=True, help="The drive's spec name.")
    outcome_p.add_argument("--vault", required=True, help="Vault holding the spec's records.")
    outcome_p.add_argument("--output-file", required=True, metavar="PATH", help="The session's captured final output.")
    outcome_p.add_argument("--json", action="store_true", default=False, help="Print JSON (required).")

    broker_p = subparsers.add_parser(
        "broker-header",
        help="Print the Authorization header for a session's broker credentials (JSON, for a header helper).",
    )
    broker_p.add_argument(
        "--credential-file", required=True, metavar="PATH", help="The session's broker-client.json."
    )

    return parser


def main() -> int:
    """Entry point. Returns an int exit code."""
    parser = _build_parser()
    args, unrecognised = parser.parse_known_args()
    # The registration step ignores whatever its caller passes (it takes nothing from it);
    # every other command rejects stray arguments as argparse would.
    if unrecognised and args.command != "gateway":
        parser.error(f"unrecognized arguments: {' '.join(unrecognised)}")

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
        "core": _cmd_core,
        "session-start": _cmd_session_start,
        "drive-groups": _cmd_drive_groups,
        "read-outcome": _cmd_read_outcome,
        "broker-header": _cmd_broker_header,
        "gateway": _cmd_gateway,
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
