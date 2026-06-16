"""Async workspace provisioning for camp — Slice 3.

camp ai brings a workspace up in two phases:

  1. Synchronous seed (seed_pending_workspace): create the workspace dir and
     write a manifest listing every member with provision_state="pending". This
     is fast — no git fetch, no worktree add — so the harness launch is not blocked.

  2. Detached provision (spawn_detached_provisioner): spawn `camp setup --background`
     in its own session (start_new_session=True, stdin=DEVNULL, std streams →
     setup.log 0o600) that survives the parent's os.execvp into the harness. The
     U1 assumption (validated) is that this detached child runs to completion after
     the parent process image is replaced — no double-fork needed.

bring_up_workspace ties the two together: seed, then spawn.

The actual per-member work (provision_member) runs the git fetch (under a timeout),
the worktree add, and the bootstrap, then the caller flips the member's manifest
state pending→ready or →failed+reason under the slug-scoped .reconcile.lock. It is
shared by the foreground `camp setup [--retry]` and the background provisioner —
ONE code path.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from manifest import (
    manifest_path_for,
    write_central_manifest,
)

# The camp binary this process was launched as — used to build the detached
# `camp setup --background` argv. Resolved relative to this scripts/ dir.
_CAMP_BIN = Path(__file__).resolve().parent.parent / "bin" / "camp"


def _workspace_dir(
    group_name: str, slug: str, *, env: dict[str, str] | None = None
) -> Path:
    from group_resolve import central_state_dir

    return central_state_dir(group_name, env=env) / "worktrees" / slug


# ---------------------------------------------------------------------------
# Detached spawn (U1)
# ---------------------------------------------------------------------------


def spawn_detached_provisioner(
    *,
    group_name: str | None = None,
    slug: str | None = None,
    logfile_path: str,
    _argv: list[str] | None = None,
) -> Any:
    """Spawn a detached background provisioner that survives the parent os.execvp.

    Builds the `camp setup --background <slug> --group <group_name>` argv (unless
    _argv overrides it for tests), opens logfile_path 0o600, and Popen's the child
    with start_new_session=True + stdin=DEVNULL + std streams → the logfile. The
    logfile fd is held by the child, so it survives the parent exec (U1).

    Returns the Popen object (the caller does not wait — it execs into the harness).
    """
    if _argv is not None:
        argv = _argv
    else:
        argv = [
            str(_CAMP_BIN),
            "setup",
            "--background",
            slug,
            "--group",
            group_name,
        ]

    # Open the logfile 0o600 (council/Security). opener enforces the mode at
    # creation so it is umask-proof.
    def _opener(path: str, flags: int) -> int:
        return os.open(path, flags, 0o600)

    logfile = open(logfile_path, "w", opener=_opener)
    proc = subprocess.Popen(
        argv,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=logfile,
        stderr=logfile,
    )
    return proc


# ---------------------------------------------------------------------------
# Synchronous seed
# ---------------------------------------------------------------------------


def seed_pending_workspace(
    group: dict[str, Any],
    slug: str,
    *,
    env: dict[str, str] | None = None,
) -> Path:
    """Create the workspace dir + seed the manifest with every member pending.

    Synchronous and fast — no git fetch, no worktree add. Returns the manifest path.
    Idempotent: a member already listed keeps its existing provision_state so a
    re-run of camp ai does not reset a ready member back to pending.
    """
    group_name = group["group"]["name"]
    branch_pattern = group.get("branch_pattern", "worktree-{slug}")
    branch = branch_pattern.format(slug=slug)

    ws_dir = _workspace_dir(group_name, slug, env=env)
    ws_dir.mkdir(parents=True, exist_ok=True)

    mpath = manifest_path_for(group_name, slug, env=env)

    existing_states: dict[str, dict[str, Any]] = {}
    if mpath.is_file():
        from manifest import read_central_manifest

        try:
            prior = read_central_manifest(mpath)
            for m in prior.get("members", []):
                existing_states[m["name"]] = m
        except Exception:
            existing_states = {}

    member_entries: list[dict[str, Any]] = []
    for member in group["members"]:
        name = member["name"]
        wt_path = ws_dir / name
        prior = existing_states.get(name)
        state = prior.get("provision_state", "pending") if prior else "pending"
        entry: dict[str, Any] = {
            "name": name,
            "repo_root": str(Path(member["repo_root"])),
            "worktree_path": str(wt_path),
            "provision_state": state,
        }
        if prior and prior.get("reason"):
            entry["reason"] = prior["reason"]
        member_entries.append(entry)

    manifest_data = {
        "schema_version": 1,
        "group": group_name,
        "slug": slug,
        "branch": branch,
        "members": member_entries,
    }
    write_central_manifest(mpath, manifest_data)
    return mpath


def bring_up_workspace(
    group: dict[str, Any],
    slug: str,
    *,
    env: dict[str, str] | None = None,
) -> Path:
    """camp ai bring-up: seed pending members + spawn the detached provisioner.

    Returns the manifest path. The harness launch (Slice 6) follows this call.
    """
    group_name = group["group"]["name"]
    mpath = seed_pending_workspace(group, slug, env=env)
    ws_dir = _workspace_dir(group_name, slug, env=env)
    spawn_detached_provisioner(
        group_name=group_name,
        slug=slug,
        logfile_path=str(ws_dir / "setup.log"),
    )
    return mpath


# ---------------------------------------------------------------------------
# Per-member provisioning (shared by foreground + background)
# ---------------------------------------------------------------------------


def provision_member(
    group: dict[str, Any],
    slug: str,
    member: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Provision one member: fetch base (timeout), add worktree, bootstrap.

    Raises ReconcileError / TimeoutExpired on failure — the caller flips the
    member's manifest state to failed+reason. Best-effort: a failure here is
    isolated to this member by the caller.
    """
    import reconcile
    from reconcile import (
        DEFAULT_BASE,
        FETCH_TIMEOUT_SECONDS,
        _add_worktree_for_member,
        _run_bootstrap,
    )

    group_name = group["group"]["name"]
    branch_pattern = group.get("branch_pattern", "worktree-{slug}")
    branch = branch_pattern.format(slug=slug)
    repo_root = Path(member["repo_root"])
    base = member.get("base") or DEFAULT_BASE
    wt_path = _workspace_dir(group_name, slug, env=env) / member["name"]

    # Fetch the base ref under a timeout (deferred from Slice 2). A timeout or
    # fetch failure does not abort if the base already resolves locally; the
    # subsequent add falls back to HEAD. But a TimeoutExpired propagates so an
    # unreachable remote fails the member.
    reconcile._fetch_base(repo_root, base, timeout=FETCH_TIMEOUT_SECONDS)
    _add_worktree_for_member(member, wt_path, branch, repo_root, base=base)
    _run_bootstrap(member, wt_path)
