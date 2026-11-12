"""``portage check-slice-branch`` — the unattended PR step's check on a record's branch label.

``--manifest <path> --record <name> --branch <label>`` exits 0 when ``<label>`` is exactly the
branch execute cuts for the record (the record's bare name) and is no member repo's default
branch. A member's default branch is what ``origin/HEAD`` names; ``main`` and ``master`` always
count too. Nothing is printed on success, and a refusal never echoes the label.

Exit codes: 0 accepted, 2 unreadable manifest or unusable member, 3 the label is a default
branch, 4 the label is not the record's own branch.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

EXIT_OK, EXIT_UNREADABLE, EXIT_DEFAULT_BRANCH, EXIT_NOT_RECORD_BRANCH = 0, 2, 3, 4

_CONVENTIONAL_DEFAULTS = frozenset({"main", "master"})


def add_slice_branch_subparser(sub) -> None:
    p = sub.add_parser(
        "check-slice-branch",
        help="Check that a record's craft/branch label is its own branch, not a default branch.",
        description=(
            "Exit 0 when --branch equals --record (the branch execute cuts for the record) and "
            "is no member repo's default branch; 3 when it is a default branch; 4 when it is "
            "not the record's own branch; 2 on an unreadable manifest."
        ),
    )
    p.add_argument("--manifest", required=True, help="Path to the camp central manifest.json")
    p.add_argument("--record", required=True, help="Bare record name (the `<name>` of task/<name>).")
    p.add_argument("--branch", required=True, help="The record's craft/branch label value.")
    p.set_defaults(func=cmd_check_slice_branch)


def _default_branches(worktree: str) -> set[str]:
    result = subprocess.run(
        ["git", "-C", worktree, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True,
    )
    resolved = result.stdout.strip()
    if result.returncode == 0 and resolved.startswith("origin/"):
        return {resolved.removeprefix("origin/")}
    return set()


def cmd_check_slice_branch(args: argparse.Namespace) -> int:
    try:
        members = json.loads(Path(args.manifest).read_text(encoding="utf-8"))["members"]
        worktrees = [m["worktree_path"] for m in members]
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"portage: cannot read the camp manifest: {type(e).__name__}", file=sys.stderr)
        return EXIT_UNREADABLE
    defaults = set(_CONVENTIONAL_DEFAULTS)
    for worktree in worktrees:
        defaults |= _default_branches(worktree)
    if args.branch in defaults:
        print("portage: the record's branch label is a default branch", file=sys.stderr)
        return EXIT_DEFAULT_BRANCH
    if args.branch != args.record:
        print("portage: the record's branch label is not the record's own branch", file=sys.stderr)
        return EXIT_NOT_RECORD_BRANCH
    return EXIT_OK
