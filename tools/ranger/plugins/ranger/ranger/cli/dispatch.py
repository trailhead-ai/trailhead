"""The `ranger` CLI — the single entry point for the sweep commands.

The thin ``cli/ranger`` shim puts ``plugins/ranger/`` on ``sys.path`` (so the
``ranger`` package and the plugin-root-level ``_bootstrap`` module resolve) then
calls ``main()`` here. ``main`` bootstraps ``trailhead.paths`` via ``_bootstrap``
BEFORE building the parser: the sweep state (locks, reports) is addressed through
the shared path resolvers, so no command module may be imported until the shared
library is reachable. That is why ``build_parser`` imports command modules lazily
rather than at this module's top level.

Error hygiene matches the rest of the suite: argparse's ``prog`` is ``ranger``,
so a bad verb or a missing argument exits nonzero with ``ranger: <message>`` on
stderr and never a traceback.

Run ``ranger --help`` (or ``ranger <cmd> --help``) for the authoritative,
self-describing subcommand list — this docstring deliberately does not restate it
so the two can't drift.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    from .queue import add_queue_subparser

    parser = argparse.ArgumentParser(prog="ranger", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    add_queue_subparser(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    from _bootstrap import ensure_trailhead_importable

    ensure_trailhead_importable()

    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
    return args.func(args)
