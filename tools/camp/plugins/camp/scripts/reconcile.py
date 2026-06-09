"""Worktree lifecycle reconciler for camp — Slice 2.

reconcile_worktree(group, slug):
    Idempotent create-or-reconcile. For each group member:
    - Ensures <repo_root>/.claude/worktrees/<slug> exists on branch worktree-<slug>.
    - Existence-guard before git worktree add (never blindly re-add).
    - Bootstraps each member's configured bootstrap list in parallel (shell=False).
    - Writes the central manifest atomically only after ALL members succeed.

reconcile_break(group, slug):
    Removes each member's worktree + the central manifest.
    - D-E removal confinement: target path must be is_relative_to(repo_root).
    - Dirty worktree blocks break unless force=True.
    - Break atomicity symmetry: manifest is not left listing a removed member.

A slug-scoped file lock guards concurrent reconcile_worktree calls so two
terminals racing camp <slug> don't both git-worktree-add the same path.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from manifest import (
    ManifestError,
    manifest_path_for,
    read_central_manifest,
    remove_central_manifest,
    write_central_manifest,
)

# Thread-local lock registry for concurrent-run guard (same process; file lock
# guards cross-process concurrency).
_SLUG_LOCKS: dict[str, threading.Lock] = {}
_SLUG_LOCKS_META = threading.Lock()


class ReconcileError(Exception):
    """Raised on a non-recoverable reconcile failure.

    The message always names the member and the reason.
    """


class ConfinementError(Exception):
    """Raised when a worktree path is outside the member's declared repo_root (D-E)."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _slug_lock(slug: str) -> threading.Lock:
    """Return a per-slug threading.Lock (created on demand)."""
    with _SLUG_LOCKS_META:
        if slug not in _SLUG_LOCKS:
            _SLUG_LOCKS[slug] = threading.Lock()
        return _SLUG_LOCKS[slug]


def _branch_name(slug: str, branch_pattern: str) -> str:
    """Expand the branch pattern for the slug."""
    return branch_pattern.format(slug=slug)


def _worktree_path(repo_root: Path, slug: str) -> Path:
    """Return the worktree path for the slug under repo_root."""
    return repo_root / ".claude" / "worktrees" / slug


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _git_is_dirty(path: Path) -> bool:
    result = _git(path, "status", "--porcelain")
    return bool(result.stdout.strip())


def _branch_exists_locally(repo_root: Path, branch: str) -> bool:
    result = _git(repo_root, "branch", "--list", branch)
    return bool(result.stdout.strip())


def _worktree_registered(repo_root: Path, wt_path: Path) -> bool:
    """Return True if wt_path is already listed in git's worktree registry."""
    result = _git(repo_root, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            registered = line[len("worktree "):].strip()
            try:
                if Path(registered).resolve() == wt_path.resolve():
                    return True
            except Exception:
                pass
    return False


def _add_worktree_for_member(
    member: dict[str, Any],
    wt_path: Path,
    branch: str,
    repo_root: Path,
) -> None:
    """Add a git worktree for one member.

    Existence-guard: if wt_path already exists (directory present OR already
    registered with git), skip the add — idempotent re-run.

    Raises ReconcileError on git failure.
    """
    if wt_path.is_dir() or _worktree_registered(repo_root, wt_path):
        return  # Already present — existence-guard (idempotent)

    wt_path.parent.mkdir(parents=True, exist_ok=True)

    if _branch_exists_locally(repo_root, branch):
        result = _git(repo_root, "worktree", "add", str(wt_path), branch)
    else:
        result = _git(repo_root, "worktree", "add", "-b", branch, str(wt_path), "HEAD")

    if result.returncode != 0:
        raise ReconcileError(
            f"camp: git worktree add failed for member {member['name']!r} "
            f"at {wt_path}: {result.stderr.strip() or result.stdout.strip()}"
        )


def _run_bootstrap(member: dict[str, Any], wt_path: Path) -> None:
    """Run a member's bootstrap command in the worktree directory.

    bootstrap is a flat list of strings representing a single command +
    its arguments (shell=False, D-F trust boundary). E.g.:
        ["pip", "install", "-e", "."]

    An empty list means no bootstrap; a no-op.
    Raises ReconcileError if the command exits non-zero.
    """
    bootstrap = member.get("bootstrap") or []
    if not bootstrap:
        return

    if not all(isinstance(part, str) for part in bootstrap):
        raise ReconcileError(
            f"camp: bootstrap for member {member['name']!r} must be a list of strings "
            f"(D-F: shell=False); got non-string elements"
        )

    result = subprocess.run(
        bootstrap,
        cwd=str(wt_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReconcileError(
            f"camp: bootstrap failed for member {member['name']!r} "
            f"(exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )


def _remove_worktree_for_member(
    member: dict[str, Any],
    wt_path: Path,
    repo_root: Path,
    *,
    force: bool,
) -> None:
    """Remove a git worktree for one member.

    D-E confinement: assert wt_path is_relative_to repo_root before removal.
    Raises ConfinementError if the path is outside the repo_root.
    Raises ReconcileError if git worktree remove fails.
    """
    # D-E: path confinement check
    try:
        wt_resolved = wt_path.resolve()
        root_resolved = repo_root.resolve()
        wt_resolved.relative_to(root_resolved)
    except ValueError:
        raise ConfinementError(
            f"camp: worktree path {wt_path} is outside the member {member['name']!r} "
            f"repo_root {repo_root} — refusing removal (D-E confinement)"
        )

    if not wt_path.is_dir():
        return  # Already gone — idempotent

    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(wt_path))

    result = _git(repo_root, *args)
    if result.returncode != 0:
        raise ReconcileError(
            f"camp: git worktree remove failed for member {member['name']!r} "
            f"at {wt_path}: {result.stderr.strip() or result.stdout.strip()}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def reconcile_worktree(
    group: dict[str, Any],
    slug: str,
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Idempotent create-or-reconcile a worktree set for (group, slug).

    For each member:
      1. Ensures <repo_root>/.claude/worktrees/<slug> exists on branch worktree-<slug>.
      2. Runs each member's bootstrap commands (shell=False, D-F).
    Then writes the central manifest atomically.

    Concurrent-run guard: a per-slug lock (threading + file lock) prevents two
    concurrent reconcile_worktree calls from both running git worktree add.

    Returns a result dict with:
        member_count:  int
        members:       list of {"name", "worktree_path"}
        manifest_path: str
        bootstrap:     "ok" | "skipped"

    Raises:
        ReconcileError: on git or bootstrap failure (with member name in message).
    """
    group_name: str = group["group"]["name"]
    members: list[dict[str, Any]] = group["members"]
    branch_pattern: str = group.get("branch_pattern", "worktree-{slug}")
    branch = _branch_name(slug, branch_pattern)

    # Get the slug-scoped lock (guards concurrent same-process calls)
    lock = _slug_lock(f"{group_name}/{slug}")

    with lock:
        mpath = manifest_path_for(group_name, slug, env=env)

        # Also acquire a file-level lock to guard cross-process concurrency
        lock_file_path = mpath.parent / ".reconcile.lock"
        lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = open(str(lock_file_path), "w")
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)

            # -- Phase 1: Create member worktrees (existence-guarded)
            member_results: list[dict[str, Any]] = []
            for member in members:
                repo_root = Path(member["repo_root"])
                wt_path = _worktree_path(repo_root, slug)
                _add_worktree_for_member(member, wt_path, branch, repo_root)
                member_results.append({
                    "name": member["name"],
                    "repo_root": str(repo_root),
                    "worktree_path": str(wt_path),
                })

            # -- Phase 2: Bootstrap members in parallel (shell=False, D-F)
            any_bootstrap_failure: Exception | None = None
            if any(bool(m.get("bootstrap")) for m in members):
                with ThreadPoolExecutor(max_workers=len(members)) as executor:
                    futures = {}
                    for member, mr in zip(members, member_results):
                        wt_path = Path(mr["worktree_path"])
                        fut = executor.submit(_run_bootstrap, member, wt_path)
                        futures[fut] = member

                    for fut in as_completed(futures):
                        member = futures[fut]
                        try:
                            fut.result()
                        except Exception as e:
                            any_bootstrap_failure = e
                            break

            if any_bootstrap_failure is not None:
                raise any_bootstrap_failure

            # -- Phase 3: Write central manifest atomically (only after all succeed)
            manifest_data: dict[str, Any] = {
                "schema_version": 1,
                "group": group_name,
                "slug": slug,
                "branch": branch,
                "members": member_results,
            }
            write_central_manifest(mpath, manifest_data)

        finally:
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            lock_fd.close()

    return {
        "slug": slug,
        "member_count": len(member_results),
        "members": [mr["name"] for mr in member_results],
        "manifest_path": str(mpath),
        "bootstrap": "ok",
    }


def reconcile_break(
    group: dict[str, Any],
    slug: str,
    *,
    env: dict[str, str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Remove a worktree set for (group, slug).

    Algorithm:
      1. Read the central manifest to get member worktree paths.
      2. Check all members for dirty trees (abort unless force=True).
      3. D-E confinement: assert each worktree path is_relative_to its repo_root.
      4. Remove each member worktree via git worktree remove.
      5. Remove the central manifest ONLY if all removals succeeded (break
         atomicity symmetry — never leave a manifest listing a removed member).

    Returns a result dict with status, removed members, and any errors.

    Raises:
        ManifestError: If the manifest is malformed.
        ConfinementError: If a worktree path is outside its repo_root (D-E).
        ReconcileError: If a member worktree is dirty and force=False.
    """
    group_name: str = group["group"]["name"]
    mpath = manifest_path_for(group_name, slug, env=env)

    # Read current manifest
    manifest_data = read_central_manifest(mpath)
    member_entries = manifest_data.get("members", [])

    # Dirty-check before removal
    if not force:
        dirty = []
        for entry in member_entries:
            wt_path = Path(entry["worktree_path"])
            if wt_path.is_dir() and _git_is_dirty(wt_path):
                dirty.append(entry["name"])
        if dirty:
            raise ReconcileError(
                f"camp: dirty worktrees in slug {slug!r}: {', '.join(dirty)} "
                "(pass force=True to discard changes)"
            )

    # D-E confinement pre-check: validate all paths BEFORE removing anything
    for entry in member_entries:
        wt_path = Path(entry["worktree_path"])
        repo_root = Path(entry["repo_root"])
        try:
            wt_path.resolve().relative_to(repo_root.resolve())
        except ValueError:
            raise ConfinementError(
                f"camp: worktree path {wt_path} is outside the member {entry['name']!r} "
                f"repo_root {repo_root} — refusing removal (D-E confinement)"
            )

    # Remove each member worktree; track removals for atomicity symmetry
    removed: list[str] = []
    errors: list[str] = []
    member_for_entry: dict[str, dict[str, Any]] = {}

    # Build a lookup from name to group config member
    for m in group["members"]:
        member_for_entry[m["name"]] = m

    for entry in member_entries:
        name = entry["name"]
        wt_path = Path(entry["worktree_path"])
        repo_root = Path(entry["repo_root"])
        member = member_for_entry.get(name, {"name": name})

        try:
            _remove_worktree_for_member(member, wt_path, repo_root, force=force)
            removed.append(name)
        except ConfinementError:
            raise
        except Exception as e:
            errors.append(f"{name}: {e}")

    # Break atomicity symmetry: only remove the manifest if all removals succeeded
    # (or if the members that failed were already absent).
    # Do NOT leave a manifest listing members whose worktrees are already removed.
    if not errors:
        remove_central_manifest(mpath)
        status = "ok"
    else:
        # Some removals failed. Update the manifest to reflect reality:
        # remove entries for members that were successfully removed so the
        # manifest never lists a member whose worktree is gone.
        remaining = [
            e for e in member_entries
            if e["name"] not in removed
        ]
        if remaining:
            updated = dict(manifest_data)
            updated["members"] = remaining
            write_central_manifest(mpath, updated)
        else:
            remove_central_manifest(mpath)
        status = "ok_with_errors"

    return {
        "status": status,
        "slug": slug,
        "removed": removed,
        "errors": errors,
    }


def format_success_summary(result: dict[str, Any]) -> str:
    """Format a D-I success summary line for camp <slug>.

    Format: "worktree-<slug>: N member(s) — <names> (bootstrap: ok) | manifest: <path>"
    """
    slug = result.get("slug", "?")
    count = result.get("member_count", 0)
    members = result.get("members", [])
    names = ", ".join(members) if members else "(none)"
    bootstrap_status = result.get("bootstrap", "ok")
    manifest_path = result.get("manifest_path", "?")

    # Derive branch name from manifest path or members
    # The slug is embedded in the manifest path as .../<slug>/manifest.json
    return (
        f"worktree-{slug}: {count} member(s) — {names} "
        f"(bootstrap: {bootstrap_status}) | manifest: {manifest_path}"
    )
