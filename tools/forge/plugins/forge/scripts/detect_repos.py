#!/usr/bin/env python3
"""Detect active repos in a camp group worktree.

Usage: detect_repos.py --manifest <path>

Reads the camp central manifest at <path> (stdlib json, not camp module — B-1
self-containment), inspects each member's worktree_path for an active feature
branch with uncommitted or unpushed work, and returns a JSON array:

  [{"repo": "<name>", "path": "<worktree_path>", "branch": "<branch>",
    "ahead": <int>, "dirty": <int>}, ...]

A member whose worktree_path does not exist is silently skipped (R-7 graceful
degrade — a gone sibling doesn't fail the group).

All git calls go through the injectable runner (R-1, S-4 shell=False).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import runner_protocol as rp


class ManifestReadError(Exception):
    """Raised on a missing or malformed manifest (path always in the message)."""


def _load_manifest(manifest_path: str) -> dict[str, Any]:
    """Load and parse the camp central manifest using stdlib json."""
    p = Path(manifest_path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise ManifestReadError(
            f"detect_repos: cannot read manifest at {manifest_path}: {e}"
        ) from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ManifestReadError(
            f"detect_repos: malformed manifest at {manifest_path}: {e}"
        ) from e
    if not isinstance(data, dict):
        raise ManifestReadError(
            f"detect_repos: manifest at {manifest_path} is not a JSON object"
        )
    return data


def _inspect(repo_name: str, worktree_path: str, *, runner: rp.Runner) -> dict | None:
    """Return a repo-status dict for worktree_path, or None if no active work.

    Checks branch, commits-ahead-of-origin/main, and dirty files.
    Returns None if on main, rev-parse fails, or there is no work.
    """
    r = rp.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=worktree_path,
        runner=runner,
    )
    if r.returncode != 0:
        return None
    branch = r.stdout.strip()
    if not branch or branch == "main":
        return None

    ahead_r = rp.run(
        ["git", "rev-list", "origin/main..HEAD", "--count"],
        cwd=worktree_path,
        runner=runner,
    )
    ahead = int(ahead_r.stdout.strip() or "0") if ahead_r.returncode == 0 else 0

    dirty_r = rp.run(
        ["git", "status", "--porcelain"],
        cwd=worktree_path,
        runner=runner,
    )
    dirty_lines = dirty_r.stdout.splitlines() if dirty_r.returncode == 0 else []
    dirty = len([line for line in dirty_lines if line.strip()])

    if ahead == 0 and dirty == 0:
        return None

    return {
        "repo": repo_name,
        "path": worktree_path,
        "branch": branch,
        "ahead": ahead,
        "dirty": dirty,
    }


def detect_repos(
    manifest_path: str,
    *,
    runner: rp.Runner | None = None,
) -> list[dict]:
    """Core function: load manifest and inspect each member's worktree.

    Args:
        manifest_path:  Absolute path to the camp central manifest.json.
        runner:         Injectable runner for tests (default: real subprocess).

    Returns:
        List of active-repo dicts. Empty if no member has active work.

    Raises:
        ManifestReadError: On a missing or malformed manifest.
    """
    data = _load_manifest(manifest_path)
    members = data.get("members", [])

    effective_runner = runner if runner is not None else rp._default_runner

    active = []
    for member in members:
        name = member.get("name", "")
        wt_path = member.get("worktree_path", "")

        # R-7: graceful degrade — missing worktree_path → skip, exit 0
        if not wt_path or not Path(wt_path).exists():
            continue

        entry = _inspect(name, wt_path, runner=effective_runner)
        if entry is not None:
            active.append(entry)

    return active


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Detect active repos in a camp group worktree.")
    ap.add_argument("--manifest", required=True, help="Path to the camp central manifest.json")
    args = ap.parse_args(argv)

    try:
        result = detect_repos(args.manifest)
    except ManifestReadError as e:
        print(str(e), file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
