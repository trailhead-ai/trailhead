#!/usr/bin/env python3
"""Footprint guard for the whole-change simplify phase.

Mechanically enforces the simplify phase's write scope: the simplifier may
touch (edit, add, delete) only files that were already part of this change's
footprint, never files outside it. Prose charters aren't enough to guarantee
this — this script is the mechanical check the simplifier runs before
committing, and the check the controller re-runs after a commit lands.

Usage:
    footprint_guard.py <base-sha> <pre-simplify-sha> <post-simplify-ref>

Runs against the git repository rooted at the current working directory.

Footprint = the set of files touched in the `base..pre-simplify` diff — i.e.
everything the tasks legitimately changed before the simplify phase started.

"Post-simplify state" is the union of:
  - files touched in the `pre-simplify..post-simplify-ref` diff (covers the
    case where the simplifier has already committed its change), and
  - any uncommitted working-tree drift — staged, unstaged, and untracked
    files (covers the case where this guard runs *before* the simplifier's
    own commit, per its charter, so post-simplify-ref may equal
    pre-simplify-sha with the real delta still sitting in the working tree).

Exit codes:
    0  clean — every post-simplify-touched file lies within the footprint
    1  violation — at least one file touched outside the footprint (each
       offending path is printed to stdout, one per line)
    2  error — fail-closed: not a git repository, or a SHA/ref could not be
       resolved. NEVER exits 0 when it could not actually certify the tree.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _err(msg: str) -> None:
    print(f"footprint-guard: {msg}", file=sys.stderr)


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _is_git_repo(cwd: Path) -> bool:
    r = _git(["rev-parse", "--is-inside-work-tree"], cwd)
    return r.returncode == 0 and r.stdout.strip() == "true"


def _resolve_commit(rev: str, cwd: Path) -> str | None:
    r = _git(["rev-parse", "--verify", "-q", f"{rev}^{{commit}}"], cwd)
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def _diff_files(a: str, b: str, cwd: Path) -> set[str]:
    r = _git(["diff", "--name-only", a, b], cwd)
    return {ln for ln in r.stdout.splitlines() if ln}


def _working_tree_files(cwd: Path) -> set[str]:
    """Staged, unstaged, and untracked paths not yet committed. Renames are
    disabled (--no-renames) so every record is a single NUL-terminated path —
    the pinned test contract has no rename case and a rename still surfaces
    correctly as a delete+add pair."""
    r = _git(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"],
        cwd,
    )
    return {entry[3:] for entry in r.stdout.split("\0") if entry}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Footprint guard for the whole-change simplify phase.")
    ap.add_argument("base_sha", help="base SHA the change started from")
    ap.add_argument("pre_simplify_sha", help="commit SHA immediately before the simplify phase")
    ap.add_argument("post_simplify_ref", help="ref/commit-ish for the simplify phase's resulting state")
    args = ap.parse_args(argv)

    cwd = Path.cwd()
    if not _is_git_repo(cwd):
        _err(f"not a git repository: {cwd}")
        return 2

    base = _resolve_commit(args.base_sha, cwd)
    if base is None:
        _err(f"cannot resolve base SHA: {args.base_sha}")
        return 2
    pre = _resolve_commit(args.pre_simplify_sha, cwd)
    if pre is None:
        _err(f"cannot resolve pre-simplify SHA: {args.pre_simplify_sha}")
        return 2
    post = _resolve_commit(args.post_simplify_ref, cwd)
    if post is None:
        _err(f"cannot resolve post-simplify ref: {args.post_simplify_ref}")
        return 2

    footprint = _diff_files(base, pre, cwd)
    touched = _diff_files(pre, post, cwd) | _working_tree_files(cwd)
    offending = sorted(touched - footprint)

    if offending:
        for path in offending:
            print(path)
        _err(f"{len(offending)} file(s) touched outside the footprint — commit blocked")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
