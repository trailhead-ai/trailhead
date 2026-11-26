"""The `docket` command line: one dispatch table of subcommands behind an argparse parser.

Register a subcommand by adding a ``Command`` to ``COMMANDS`` under its name; ``main`` builds the
parser from the table, so nothing else changes.

Error convention: a failure prints ``docket: <message>`` on stderr and exits nonzero, never a
traceback. A command signals one by raising ``DocketError`` (exit 1); a usage error (unknown
command or flag) exits 2.
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable, NamedTuple, NoReturn, Sequence

from docket.errors import DocketError


class Command(NamedTuple):
    help: str
    add_arguments: Callable[[argparse.ArgumentParser], None]
    run: Callable[[argparse.Namespace], int | None]


COMMANDS: dict[str, Command] = {}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        print(f"docket: {message}", file=sys.stderr)
        raise SystemExit(2)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="docket", description="Keep the project's docket from the terminal.")
    sub = parser.add_subparsers(dest="command", metavar="<command>", parser_class=_Parser)
    for name, command in COMMANDS.items():
        command.add_arguments(sub.add_parser(name, help=command.help, description=command.help))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    if args_in and not args_in[0].startswith("-") and args_in[0] not in COMMANDS:
        parser.error(f"unknown command {args_in[0]!r}")
    args = parser.parse_args(args_in)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return COMMANDS[args.command].run(args) or 0
    except DocketError as exc:
        print(f"docket: {exc}", file=sys.stderr)
        return 1
