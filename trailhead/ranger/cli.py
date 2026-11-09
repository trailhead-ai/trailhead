"""ranger — the per-account hub CLI. Errors print ``ranger: <message>`` on stderr."""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from trailhead.launch_profile import LaunchProfileError, hub_config_path, validate_account
from trailhead.paths import PathResolutionError
from trailhead.ranger.config import (
    DEFAULT_LAUNCH_ACCOUNT,
    HubConfig,
    HubConfigError,
    camp_declared_launch_accounts,
    expand_home,
    list_hub_accounts,
    read_hub_config,
    write_hub_config,
)
from trailhead.ranger import outpost_client
from trailhead.ranger.outpost_client import OutpostRefusal


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        print(f"ranger: {message}", file=sys.stderr)
        raise SystemExit(2)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="ranger", description="Per-account hub CLI.")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    setup = sub.add_parser("setup", help="Configure a hub for an account (once per account).")
    setup.add_argument("--account", required=True, help="Account short name.")
    setup.add_argument(
        "--launch-account",
        required=True,
        help="Camp launch account the hub runs under: 'default' or an absolute or ~-prefixed path.",
    )
    setup.add_argument("--name", required=True, help="Hub name.")
    start = sub.add_parser("start", help="Start the account's hub through Outpost and attach to it.")
    start.add_argument("--account", help="Account short name (optional when one hub is configured).")
    return parser


def _setup(args: argparse.Namespace, env: dict[str, str]) -> int:
    config = HubConfig(name=args.name, account=args.account, launch_account=args.launch_account)
    wrote = write_hub_config(config, env=env)
    path = hub_config_path(config.account, env=env)
    if config.launch_account != DEFAULT_LAUNCH_ACCOUNT:
        declared = camp_declared_launch_accounts(env=env)
        if expand_home(config.launch_account, env.get("HOME", "")) not in declared:
            print(
                f"ranger: warning: no camp group declares launch account "
                f"{config.launch_account!r}; the hub will still be configured with it",
                file=sys.stderr,
            )
    print(f"{'configured' if wrote else 'already configured'} hub {config.name!r} ({path})")
    return 0


def _select_account(args: argparse.Namespace, env: dict[str, str]) -> str:
    if args.account:
        return validate_account(args.account)
    accounts = list_hub_accounts(env=env)
    if not accounts:
        raise OutpostRefusal("no hub is configured; run `ranger setup` first")
    if len(accounts) > 1:
        raise OutpostRefusal(
            "more than one hub is configured; name one with --account: " + ", ".join(accounts)
        )
    return accounts[0]


def _start(
    args: argparse.Namespace,
    env: dict[str, str],
    *,
    port: int,
    request_timeout: float,
    exec_fn,
) -> int:
    account = _select_account(args, env)
    config = read_hub_config(account, env=env)
    if not outpost_client.outpost_answering(port):
        raise OutpostRefusal("Outpost is not running; start it with `trailhead outpost start`")
    if shutil.which("tmux", path=env.get("PATH", "")) is None:
        raise OutpostRefusal("tmux is not installed or not on PATH; ranger attaches to the hub through tmux")
    credential = outpost_client.read_operator_credential(env)
    outpost_client.start_hub(account, credential, port=port, timeout=request_timeout)
    nested = "TMUX" in env
    detach = (
        "detach the inner tmux with the prefix twice, then d"
        if nested
        else "detach with the tmux prefix, then d"
    )
    print(f"attaching to hub {config.name!r} (hub-{account}); {detach} (the hub keeps running)")
    sys.stdout.flush()
    exec_env = {k: v for k, v in env.items() if k != "TMUX"}
    exec_fn(outpost_client.tmux_attach_argv(account), exec_env)
    return 0


def _exec(argv: list[str], env: dict[str, str]) -> None:
    os.execvpe(argv[0], argv, env)


def main(
    argv: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    port: int = outpost_client.DAEMON_PORT,
    request_timeout: float = outpost_client.START_TIMEOUT_SECONDS,
    exec_fn=_exec,
) -> int:
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    environ = env if env is not None else dict(os.environ)
    try:
        if args.command == "setup":
            return _setup(args, environ)
        if args.command == "start":
            return _start(args, environ, port=port, request_timeout=request_timeout, exec_fn=exec_fn)
    except (OutpostRefusal, HubConfigError, LaunchProfileError, PathResolutionError) as exc:
        print(f"ranger: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
