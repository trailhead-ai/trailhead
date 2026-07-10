"""Group-aware lifecycle commands for camp.

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
import sys
from pathlib import Path
from typing import Any

from ..group.resolve import central_state_dir
from ..group.manifest import (
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
        from ..group.manifest import manifest_path_for

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

        worktrees.append(
            {
                "slug": slug_name,
                "branch": data.get("branch", ""),
                "manifest_path": str(mpath),
                "members": member_statuses,
                "dev_env_instance": None,
                "fire_state": None,
            }
        )

    return {"worktrees": worktrees}


def cmd_ls_group(
    group: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return a list of all worktrees for the group.

    Returns:
        [
            {
                "slug": str,
                "branch": str,
                "manifest_path": str,
                "group": str,
                "workspace_path": str,  # absolute path; same as workspace_dir(group, slug)
            },
            ...
        ]
    """
    pairs = _list_group_worktrees(group, env=env)
    entries = []
    for slug_name, mpath in pairs:
        try:
            data = read_central_manifest(mpath)
            entries.append(
                {
                    "slug": slug_name,
                    "branch": data.get("branch", ""),
                    "manifest_path": str(mpath),
                    "group": data.get("group", ""),
                    # mpath is <workspace_dir>/manifest.json, so its parent IS the
                    # workspace dir — no need to recompute workspace_dir() (which
                    # re-runs central_state_dir) per row.
                    "workspace_path": str(mpath.parent),
                }
            )
        except ManifestError:
            pass
    return entries


# Fixed JSON schema for `camp list --json`, emitted identically by BOTH entry
# points so a parser never KeyErrors switching between them.
_LIST_JSON_KEYS = ("slug", "branch", "workspace_path", "group")


def render_workspace_list(entries: list[dict[str, Any]], *, as_json: bool) -> None:
    """Single renderer for `camp list`/`ls` output — consulted by BOTH dispatchers
    (cli/camp's group-aware `_cmd_ls_group_cli` and spine.main's no-group `cmd_ls`)
    so the human + --json surface is identical regardless of cwd.

    Each entry must carry `slug` and `workspace_path`; `branch` and `group` are
    optional (group is None for the standalone fallback). Output:
      - human: one `slug workspace_path` line per entry; empty → no stdout.
      - --json: a list of {slug, branch, workspace_path, group} dicts (the fixed
        _LIST_JSON_KEYS schema); empty → `[]`.

    The renderer PROJECTS each entry onto the fixed schema (ignoring any
    source-specific extras like manifest_path), so the two data models — group
    central manifests vs. the legacy worktree registry — surface one stable shape.
    """
    import json as _json

    if as_json:
        rows = [
            {
                "slug": e["slug"],
                "branch": e.get("branch", ""),
                "workspace_path": e["workspace_path"],
                "group": e.get("group"),
            }
            for e in entries
        ]
        print(_json.dumps(rows))
        return

    for e in entries:
        print(f"{e['slug']} {e['workspace_path']}")


def _provision_member_and_flip(
    group: dict[str, Any],
    slug: str,
    member: dict[str, Any],
    entry: dict[str, Any],
    mpath: Path,
    *,
    env: dict[str, str] | None,
    retrying_ready: bool = False,
) -> tuple[dict[str, Any], list[Any] | None]:
    """Run one member's per-member provision under the held reconcile lock and
    flip its manifest state accordingly.

    Returns (result, task_results): `result` is the per-member entry for
    cmd_setup_group's return map ({"provision_state", "reason"?}); `task_results`
    is the list of TaskResults on the success path (so the caller can classify a
    retry outcome), or None when provisioning raised.

    A required-task failure (TaskError) flips the member to failed and persists
    the partial results; a git fetch/add failure flips it to failed with a
    reason; an optional-task failure leaves the member ready with the failed task
    recorded and warned. The caller MUST already hold the .reconcile.lock.

    `retrying_ready=True` marks this call as a retry of an outstanding OPTIONAL
    task on a member that is ALREADY ready (a required-task failure already
    keeps a member out of ready in the first place, so this call can only ever
    be chasing an optional task). Before this flag existed, `camp setup` never
    touched an already-ready member at all, so it could never regress one; the
    outstanding-task retry must preserve that invariant — a git fetch timeout or
    any other exception encountered DURING the retry is infrastructure noise
    incidental to the retry attempt, not a reason to demote an already-known-good
    member. So in this mode every exception branch leaves provision_state
    "ready" (persisting partial TaskError results into `tasks` if any were
    produced) and warns to stderr instead of flipping to failed; the returned
    result carries an internal `_retry_failed` marker (popped by the caller) so
    `cmd_setup_group` can still classify the outcome as "still-failing".
    """
    from ..group.manifest import flip_member_state_unlocked
    from . import provision
    from .reconcile import (
        _completed_from_tasks_map,
        _tasks_map_from_results,
        _warn_optional_task_failures,
    )
    from .tasks import TaskError

    name = member["name"]
    completed = _completed_from_tasks_map(entry.get("tasks"))
    tasks_kwarg: dict[str, Any] | None = None
    try:
        task_results = provision.provision_member(
            group, slug, member, completed=completed, env=env
        )
    except subprocess.TimeoutExpired as e:
        reason = f"git fetch timeout after {e.timeout}s"
    except TaskError as e:
        # A required task failed: persist its (and any prior) results alongside
        # the failed state so `camp status` shows the task.
        reason = str(e)
        tasks_kwarg = _tasks_map_from_results(e.results)
    except Exception as e:
        reason = str(e)
    else:
        _warn_optional_task_failures(task_results, name)
        flip_member_state_unlocked(
            mpath, name, "ready", tasks=_tasks_map_from_results(task_results)
        )
        return {"provision_state": "ready"}, task_results

    # Reached only when provisioning raised — one of the three `except` clauses
    # above set `reason` (and, for TaskError, `tasks_kwarg`).
    if retrying_ready:
        # A retry of an outstanding OPTIONAL task on an already-ready member
        # never demotes it — see the docstring above. A required-task failure
        # can't actually reach here (defensive only), but its partial results
        # are still persisted rather than silently dropped.
        if tasks_kwarg:
            flip_member_state_unlocked(mpath, name, "ready", tasks=tasks_kwarg)
        print(
            f"camp: retry for member {name!r} hit {reason} — member remains "
            "ready; run `camp status` for details.",
            file=sys.stderr,
        )
        return {"provision_state": "ready", "_retry_failed": True}, None

    flip_kwargs: dict[str, Any] = {"reason": reason}
    if tasks_kwarg:
        flip_kwargs["tasks"] = tasks_kwarg
    flip_member_state_unlocked(mpath, name, "failed", **flip_kwargs)
    return {"provision_state": "failed", "reason": reason}, None


def cmd_setup_group(
    group: dict[str, Any],
    slug: str,
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Foreground provisioning: complete/restart member worktrees idempotently.

    Holds the slug-scoped .reconcile.lock for the whole operation so a concurrent
    background provisioner serializes (no torn manifest, no double-add). For each
    non-ready member it runs the per-member provision (fetch+add+tasks), persists
    the per-task results into the member's `tasks` map, and flips the manifest
    pending→ready or →failed+reason. A required-task failure flips the member to
    failed; an optional-task failure leaves the member ready, records the failed
    task, and warns on stderr. Best-effort: one member failing never blocks the
    others.

    Ready-member retry: a ready member that still carries a failed or never-run
    provision-phase task (an optional failure leaves a member ready) is
    re-provisioned so those outstanding tasks re-run — an already-ok task is
    skipped (run-once). A ready member whose tasks are all ok (or that has no
    tasks) is a TRUE no-op: it is not re-provisioned at all (no git fetch, no
    manifest write). Each ready member carries a `retry` outcome in the result:
        "none"          all tasks ok — nothing retried
        "fixed"         outstanding tasks retried and now all ok
        "still-failing" retried but a task is still failed — OR the retry
                        attempt itself hit an infrastructure error (git fetch
                        timeout, or any other exception from provisioning); a
                        ready member is NEVER demoted to failed by that noise,
                        since a required-task failure already keeps a member
                        out of ready in the first place, so a ready member can
                        only ever be chasing an optional task here.
    Pending/failed members provisioned normally do NOT carry a `retry` field.

    Returns {"slug", "members": {name: {"provision_state", "reason"?, "retry"?}}}.
    """
    from .reconcile import _has_outstanding_provision_tasks

    group_name = group["group"]["name"]
    mpath = manifest_path_for(group_name, slug, env=env)
    member_by_name = {m["name"]: m for m in group["members"]}

    results: dict[str, Any] = {}

    with reconcile_lock(mpath.parent):
        data = read_central_manifest(mpath)
        for entry in data.get("members", []):
            name = entry["name"]
            member = member_by_name.get(name)
            if member is None:
                # Manifest lists a member no longer in the group config.
                continue

            if entry.get("provision_state") == "ready":
                if not _has_outstanding_provision_tasks(member, entry.get("tasks")):
                    # No failed/never-run tasks — a true no-op, not re-provisioned.
                    results[name] = {"provision_state": "ready", "retry": "none"}
                    continue
                # Re-run outstanding tasks in place, then classify the outcome.
                result, task_results = _provision_member_and_flip(
                    group, slug, member, entry, mpath, env=env, retrying_ready=True
                )
                retry_failed = result.pop("_retry_failed", False)
                still_failing = (
                    retry_failed
                    or result["provision_state"] != "ready"
                    or any(r.state == "failed" for r in (task_results or []))
                )
                result["retry"] = "still-failing" if still_failing else "fixed"
                results[name] = result
                continue

            result, _ = _provision_member_and_flip(group, slug, member, entry, mpath, env=env)
            results[name] = result

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

    Exit codes are driven purely by each member's provision_state, NOT by
    individual task states: a ready member with a failed OPTIONAL task stays
    exit 0 (a failed REQUIRED task already flips the member itself to failed).
    Each member carries its persisted per-task state map (`tasks`, always
    present — an empty dict when the member has no tasks) so callers can surface
    per-task detail without changing exit-code semantics.

    report = {
        "slug", "code",
        "members": [{"name", "provision_state", "tasks", "reason"?}],
    }, where `tasks` is the manifest's {task-name: {"state", "reason"?}} map.
    """
    group_name = group["group"]["name"]
    mpath = manifest_path_for(group_name, slug, env=env)
    data = read_central_manifest(mpath)

    members = []
    any_failed = False
    any_pending = False
    for entry in data.get("members", []):
        state = entry.get("provision_state", "pending")
        m: dict[str, Any] = {
            "name": entry["name"],
            "provision_state": state,
            "tasks": entry.get("tasks") or {},
        }
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
            capture_output=True,
            text=True,
            check=False,
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
                capture_output=True,
                text=True,
                check=False,
            )
            r = subprocess.run(
                ["git", "-C", str(repo_root), "reset", "--hard", "origin/main"],
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            r = subprocess.run(
                ["git", "-C", str(repo_root), "merge", "--ff-only", "origin/main"],
                capture_output=True,
                text=True,
                check=False,
            )

        if r.returncode != 0:
            members_result[name] = {"action": "error", "error": r.stderr.strip()}
            errors += 1
        else:
            members_result[name] = {"action": "ff" if not force else "reset-force"}

    status = "ok" if errors == 0 else "ok_with_warnings"
    return {"status": status, "members": members_result}
