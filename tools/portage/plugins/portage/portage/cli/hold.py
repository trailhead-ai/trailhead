"""``portage hold`` / ``portage release`` — the merge hold's operator surface.

``hold <repo> <branch>`` records a hold (idempotent); ``release <repo> <branch>``
clears it. ``<repo>`` is the repo path as it appears in a ``portage merge`` pair.
``portage merge`` refuses a held branch (see ``portage.holds``). A hold is local: it is
keyed by the repo's real path and the branch in this machine's portage state directory, so it
binds only `portage merge` runs that share both, never a merge made by hand or through the
host. Exit 0 on success, 2 on a refused branch name.
"""

from __future__ import annotations

import argparse
import sys

from .. import holds


def add_hold_subparsers(sub) -> None:
    for name, op, text in (
        ("hold", holds.hold, "Hold a branch: `portage merge` refuses it until released."),
        ("release", holds.release, "Release a held branch (operator or orchestrator only)."),
    ):
        p = sub.add_parser(name, help=text, description=text)
        p.add_argument("repo", help="Repo path, as in a `portage merge` pair.")
        p.add_argument("branch", help="Head branch name (^[A-Za-z0-9._/-]+$).")
        p.set_defaults(func=cmd_hold_op, hold_op=op)


def cmd_hold_op(args: argparse.Namespace) -> int:
    try:
        args.hold_op(args.repo, args.branch)
    except holds.HoldError as e:
        print(f"portage: {e}", file=sys.stderr)
        return 2
    return 0
