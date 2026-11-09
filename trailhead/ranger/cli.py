"""ranger — the per-account hub CLI. Errors print ``ranger: <message>`` on stderr."""

from __future__ import annotations

import argparse
import os
import sys

from trailhead.launch_profile import LaunchProfileError, hub_config_path
from trailhead.paths import PathResolutionError
from trailhead.ranger.config import (
    DEFAULT_LAUNCH_ACCOUNT,
    HubConfig,
    HubConfigError,
    camp_declared_launch_accounts,
    expand_home,
    write_hub_config,
)


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


def main(argv: list[str] | None = None, *, env: dict[str, str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    environ = env if env is not None else dict(os.environ)
    try:
        if args.command == "setup":
            return _setup(args, environ)
    except (HubConfigError, LaunchProfileError, PathResolutionError) as exc:
        print(f"ranger: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
