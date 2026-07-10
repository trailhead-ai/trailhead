"""Shared low-level git wrappers for camp.

These are the byte-identical git helpers that were previously duplicated across
``spine.py``, ``provision/lifecycle.py``, and ``provision/reconcile.py``. They
all shell out with ``shell=False`` (list argv) and never raise on non-zero exit —
callers inspect ``returncode`` / stdout themselves.

Kept as underscore-prefixed names because every consumer imports them verbatim
(and tests monkeypatch them on the importing module, e.g. ``reconcile._git_is_dirty``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a list-arg `git -C <repo_root> ...` (shell=False) and return the result."""
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _git_out(repo_root: Path, *args: str) -> str:
    """Return stripped stdout of a git command, or "" on non-zero exit."""
    result = _git(repo_root, *args)
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_is_dirty(path: Path) -> bool:
    """True if the working tree at `path` has uncommitted changes."""
    return bool(_git(path, "status", "--porcelain").stdout.strip())


def _git_repo_status(wt_path: Path) -> dict[str, Any]:
    """Return branch / dirty-file count / unpushed-commit count for a worktree."""
    path_str = str(wt_path)
    if not wt_path.is_dir():
        return {"present": False, "path": path_str}

    branch = _git_out(wt_path, "rev-parse", "--abbrev-ref", "HEAD") or "unknown"
    dirty_raw = _git(wt_path, "status", "--porcelain").stdout
    dirty_files = len([ln for ln in dirty_raw.splitlines() if ln.strip()])
    ahead_raw = _git(wt_path, "rev-list", "--count", "@{upstream}..HEAD")
    unpushed_commits = (
        int(ahead_raw.stdout.strip())
        if ahead_raw.returncode == 0 and ahead_raw.stdout.strip().isdigit()
        else 0
    )
    last_commit = _git_out(wt_path, "log", "-1", "--oneline")

    return {
        "present": True,
        "path": path_str,
        "branch": branch,
        "dirty_files": dirty_files,
        "unpushed_commits": unpushed_commits,
        "last_commit": last_commit,
    }
