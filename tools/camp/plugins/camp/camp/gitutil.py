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


def _git_branch_drift(wt_path: Path, base: str) -> dict[str, Any]:
    """Return branch name, ahead/behind counts vs `base`, and upstream status.

    No fetch: `ahead`/`behind` are commit counts against whatever `base`
    resolves to locally right now — freshness is the caller's job (fetch
    before calling this, if that's what's wanted; status itself stays
    offline and cheap). `ahead`/`behind` are `None` when the worktree
    directory is absent, `base` doesn't resolve locally, or a resolved
    `rev-list --count` result isn't a plain integer (guarded the same way
    `_git_repo_status` guards its own count, rather than raising). `upstream`
    is `"ok"` when the branch has a configured upstream that still resolves,
    `"gone"` when an upstream is configured but no longer resolves, and
    `"none"` when no upstream is configured (also the answer when the
    worktree is absent, since there is no branch to check).

    Counts are taken against `HEAD` as the tip, never the branch's ref NAME:
    git's ref-disambiguation order checks `refs/tags/<name>` before
    `refs/heads/<name>`, so a tag sharing the branch's name would otherwise
    resolve `rev-list`'s range ambiguously instead of the true branch tip.
    A detached HEAD still reports its usual `branch` value (`"HEAD"` from
    `git rev-parse --abbrev-ref HEAD`) with counts computed correctly against
    it, since HEAD as the tip needs no branch ref to resolve.
    """
    if not wt_path.is_dir():
        return {"branch": None, "ahead": None, "behind": None, "upstream": "none"}

    branch = _git_out(wt_path, "rev-parse", "--abbrev-ref", "HEAD") or "unknown"

    base_ref = _git(wt_path, "rev-parse", "--verify", "--quiet", base)
    if base_ref.returncode != 0:
        ahead: int | None = None
        behind: int | None = None
    else:
        ahead_raw = _git(wt_path, "rev-list", "--count", f"{base}..HEAD")
        behind_raw = _git(wt_path, "rev-list", "--count", f"HEAD..{base}")
        ahead = (
            int(ahead_raw.stdout.strip())
            if ahead_raw.returncode == 0 and ahead_raw.stdout.strip().isdigit()
            else None
        )
        behind = (
            int(behind_raw.stdout.strip())
            if behind_raw.returncode == 0 and behind_raw.stdout.strip().isdigit()
            else None
        )

    merge_ref = _git_out(wt_path, "config", f"branch.{branch}.merge")
    if not merge_ref:
        upstream = "none"
    else:
        upstream_check = _git(
            wt_path, "rev-parse", "--verify", "--quiet", f"{branch}@{{upstream}}"
        )
        upstream = "ok" if upstream_check.returncode == 0 else "gone"

    return {"branch": branch, "ahead": ahead, "behind": behind, "upstream": upstream}
