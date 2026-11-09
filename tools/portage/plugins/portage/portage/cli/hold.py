"""``portage hold`` / ``portage release`` — the merge hold's operator surface.

``hold <repo> <branch>`` records a hold (idempotent); ``release <repo> <branch>``
clears it. ``<repo>`` is the repo path as it appears in a ``portage merge`` pair.
``portage merge`` refuses a held branch (see ``portage.holds``). Exit 0 on
success, 2 on a refused branch name.
"""

from __future__ import annotations

import argparse
import sys

from .. import holds


def add_hold_subparsers(sub) -> None:
    for name, func, text in (
        ("hold", cmd_hold, "Hold a branch: `portage merge` refuses it until released."),
        ("release", cmd_release, "Release a held branch (operator or orchestrator only)."),
    ):
        p = sub.add_parser(name, help=text, description=text)
        p.add_argument("repo", help="Repo path, as in a `portage merge` pair.")
        p.add_argument("branch", help="Head branch name (^[A-Za-z0-9._/-]+$).")
        p.set_defaults(func=func)


def cmd_hold(args: argparse.Namespace) -> int:
    try:
        holds.hold(args.repo, args.branch)
    except holds.HoldError as e:
        print(f"portage: {e}", file=sys.stderr)
        return 2
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    try:
        holds.release(args.repo, args.branch)
    except holds.HoldError as e:
        print(f"portage: {e}", file=sys.stderr)
        return 2
    return 0
