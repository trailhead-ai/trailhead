"""Group-aware lifecycle commands for camp — Slice 2.

These functions replace the SIBLING_REPOS-constant-based implementations in
spine.py with group-config-driven equivalents. They operate on the central
manifest and the group member list.

cmd_status_group(group, slug, env):
    Fleet view (slug=None) or scoped (slug=<name>) status across group members.

cmd_ls_group(group, env):
    List all worktrees for the group (reads the central state dir).

cmd_sync_group(group, env):
    Sync canonical member repos to latest (fetch + ff-only).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from group_resolve import central_state_dir
from manifest import (
    ManifestError,
    manifest_path_for,
    read_central_manifest,
    reconcile_lock,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _git_out(repo_root: Path, *args: str) -> str:
    result = _git(repo_root, *args)
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_is_dirty(path: Path) -> bool:
    result = _git(path, "status", "--porcelain")
    return bool(result.stdout.strip())


def _git_repo_status(wt_path: Path) -> dict[str, Any]:
    path_str = str(wt_path)
    if not wt_path.is_dir():
        return {"present": False, "path": path_str}

    branch = _git_out(wt_path, "rev-parse", "--abbrev-ref", "HEAD") or "unknown"
    dirty_raw = _git(wt_path, "status", "--porcelain").stdout
    dirty_files = len([ln for ln in dirty_raw.splitlines() if ln.strip()])
    ahead_raw = _git(wt_path, "rev-list", "--count", "@{upstream}..HEAD")
    unpushed = (
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
        "unpushed_commits": unpushed,
        "last_commit": last_commit,
    }


def _list_group_worktrees(
    group: dict[str, Any],
    env: dict[str, str] | None = None,
) -> list[tuple[str, Path]]:
    """Return list of (slug, manifest_path) for all created worktrees in the group."""
    group_name = group["group"]["name"]
    state_dir = central_state_dir(group_name, env=env)
    worktrees_dir = state_dir / "worktrees"

    results: list[tuple[str, Path]] = []
    if not worktrees_dir.is_dir():
        return results

    for entry in sorted(worktrees_dir.iterdir()):
        mpath = entry / "manifest.json"
        if mpath.is_file():
            results.append((entry.name, mpath))
    return results


# ---------------------------------------------------------------------------
# Public command functions
# ---------------------------------------------------------------------------


def cmd_status_group(
    group: dict[str, Any],
    slug: str | None = None,
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return status for one (slug) or all (slug=None) worktrees in the group.

    Fleet view (slug=None): lists all worktrees found in the central state dir.
    Scoped (slug=<name>): returns info for that worktree only.

    Returns:
        {
            "worktrees": [
                {
                    "slug": str,
                    "branch": str,
                    "manifest_path": str,
                    "members": [git-status-per-member],
                }
            ]
        }
    """
    if slug is not None:
        # Scoped: one worktree
        group_name = group["group"]["name"]
        from manifest import manifest_path_for
        mpath = manifest_path_for(group_name, slug, env=env)
        try:
            data = read_central_manifest(mpath)
        except ManifestError:
            raise
        entries = [(slug, mpath, data)]
    else:
        # Fleet view: all worktrees for this group
        pairs = _list_group_worktrees(group, env=env)
        entries = []
        for slug_name, mpath in pairs:
            try:
                data = read_central_manifest(mpath)
                entries.append((slug_name, mpath, data))
            except ManifestError:
                pass

    worktrees = []
    for slug_name, mpath, data in entries:
        member_statuses = []
        for m in data.get("members", []):
            wt_path = Path(m["worktree_path"])
            st = _git_repo_status(wt_path)
            member_statuses.append({"name": m["name"], **st})

        worktrees.append({
            "slug": slug_name,
            "branch": data.get("branch", ""),
            "manifest_path": str(mpath),
            "members": member_statuses,
            "dev_env_instance": None,
            "fire_state": None,
        })

    return {"worktrees": worktrees}


def cmd_ls_group(
    group: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return a list of all worktrees for the group.

    Returns:
        [{"slug": str, "branch": str, "manifest_path": str}, ...]
    """
    pairs = _list_group_worktrees(group, env=env)
    entries = []
    for slug_name, mpath in pairs:
        try:
            data = read_central_manifest(mpath)
            entries.append({
                "slug": slug_name,
                "branch": data.get("branch", ""),
                "manifest_path": str(mpath),
                "group": data.get("group", ""),
            })
        except ManifestError:
            pass
    return entries


def cmd_setup_group(
    group: dict[str, Any],
    slug: str,
    *,
    retry: bool = False,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Foreground provisioning: complete/restart member worktrees idempotently.

    Holds the slug-scoped .reconcile.lock for the whole operation so a concurrent
    background provisioner or another `camp setup --retry` serializes (no torn
    manifest, no double-add). For each non-ready member it runs the per-member
    provision (fetch+add+bootstrap) and flips the manifest pending→ready or
    →failed+reason. A ready member is left untouched. Best-effort: one member
    failing never blocks the others.

    retry=False: process pending + failed members (the default after camp ai).
    retry=True:  same selection (only non-ready) — the flag is explicit-intent
                 (re-run after a failure) and never re-touches a ready member.

    Returns {"slug", "members": {name: {"provision_state", "reason"?}}}.
    """
    from manifest import flip_member_state_unlocked
    import provision

    group_name = group["group"]["name"]
    mpath = manifest_path_for(group_name, slug, env=env)
    member_by_name = {m["name"]: m for m in group["members"]}

    results: dict[str, Any] = {}

    with reconcile_lock(mpath.parent):
        data = read_central_manifest(mpath)
        for entry in data.get("members", []):
            name = entry["name"]
            if entry.get("provision_state") == "ready":
                results[name] = {"provision_state": "ready"}
                continue

            member = member_by_name.get(name)
            if member is None:
                # Manifest lists a member no longer in the group config.
                continue

            try:
                provision.provision_member(group, slug, member, env=env)
            except subprocess.TimeoutExpired as e:
                reason = f"git fetch timeout after {e.timeout}s"
                flip_member_state_unlocked(mpath, name, "failed", reason=reason)
                results[name] = {"provision_state": "failed", "reason": reason}
            except Exception as e:
                reason = str(e)
                flip_member_state_unlocked(mpath, name, "failed", reason=reason)
                results[name] = {"provision_state": "failed", "reason": reason}
            else:
                flip_member_state_unlocked(mpath, name, "ready")
                results[name] = {"provision_state": "ready"}

    return {"slug": slug, "members": results}


def provision_status_code(
    group: dict[str, Any],
    slug: str,
    *,
    env: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Return (exit_code, report) for the provision state of a workspace.

    Exit codes (so the in-session agent can branch programmatically):
        0  all members ready
        2  some members pending (none failed)
        3  any member failed (failed takes precedence over pending)

    report = {"slug", "code", "members": [{"name", "provision_state", "reason"?}]}.
    """
    group_name = group["group"]["name"]
    mpath = manifest_path_for(group_name, slug, env=env)
    data = read_central_manifest(mpath)

    members = []
    any_failed = False
    any_pending = False
    for entry in data.get("members", []):
        state = entry.get("provision_state", "pending")
        m: dict[str, Any] = {"name": entry["name"], "provision_state": state}
        if state == "failed":
            any_failed = True
            if entry.get("reason"):
                m["reason"] = entry["reason"]
        elif state != "ready":
            any_pending = True
        members.append(m)

    if any_failed:
        code = 3
    elif any_pending:
        code = 2
    else:
        code = 0

    return code, {"slug": slug, "code": code, "members": members}


def cmd_sync_group(
    group: dict[str, Any],
    *,
    force: bool = False,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Sync canonical member repos to latest origin/main.

    Safe by default: dirty or off-main members are skipped.
    force=True: hard-reset to origin/main.

    Returns:
        {
            "status": "ok" | "ok_with_warnings",
            "members": {
                "<name>": {"action": "ff" | "skip-dirty" | "skip-off-main" | "absent" | ...}
            }
        }
    """
    members_result: dict[str, Any] = {}
    errors = 0

    for member in group["members"]:
        name = member["name"]
        repo_root = Path(member["repo_root"])

        if not (repo_root / ".git").exists() and not (repo_root / ".git").is_file():
            members_result[name] = {"action": "absent"}
            continue

        # Fetch
        subprocess.run(
            ["git", "-C", str(repo_root), "fetch", "origin", "--quiet"],
            capture_output=True, text=True, check=False,
        )

        is_dirty = _git_is_dirty(repo_root)
        branch = _git_out(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
        on_main = branch == "main"

        if not force and is_dirty:
            members_result[name] = {"action": "skip-dirty"}
            continue
        if not force and not on_main:
            members_result[name] = {"action": "skip-off-main", "branch": branch}
            continue

        if force:
            subprocess.run(
                ["git", "-C", str(repo_root), "checkout", "main", "--quiet"],
                capture_output=True, text=True, check=False,
            )
            r = subprocess.run(
                ["git", "-C", str(repo_root), "reset", "--hard", "origin/main"],
                capture_output=True, text=True, check=False,
            )
        else:
            r = subprocess.run(
                ["git", "-C", str(repo_root), "merge", "--ff-only", "origin/main"],
                capture_output=True, text=True, check=False,
            )

        if r.returncode != 0:
            members_result[name] = {"action": "error", "error": r.stderr.strip()}
            errors += 1
        else:
            members_result[name] = {"action": "ff" if not force else "reset-force"}

    status = "ok" if errors == 0 else "ok_with_warnings"
    return {"status": status, "members": members_result}
