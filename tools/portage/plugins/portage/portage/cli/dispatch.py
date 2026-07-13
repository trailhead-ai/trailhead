"""The `portage` CLI — the single entry point for the PR lifecycle commands.

The thin ``cli/portage`` shim puts ``plugins/portage/`` on ``sys.path`` (so the
``portage`` package and the plugin-root-level ``_bootstrap`` module resolve) then
calls ``main()`` here. ``main`` bootstraps ``trailhead.paths`` via ``_bootstrap``
BEFORE building the parser — the per-command modules import ``trailhead.vcs`` at
their module top, so they must not be imported until the shared library is
reachable. That is why ``build_parser`` imports them lazily rather than at this
module's top level.

Run ``portage --help`` (or ``portage <cmd> --help``) for the authoritative,
self-describing subcommand list — this docstring deliberately does not restate it
so the two can't drift.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    # Imported here (not at module top) so the trailhead.vcs imports these command
    # modules carry only fire after main() has run ensure_trailhead_importable() —
    # importing them earlier would touch trailhead before the bootstrap walk.
    from . import ci, pr, repos

    parser = argparse.ArgumentParser(prog="portage", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    repos.add_repos_subparser(sub)
    pr.add_pr_subparsers(sub)
    ci.add_ci_subparser(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    from _bootstrap import ensure_trailhead_importable

    ensure_trailhead_importable()

    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
    return args.func(args)
