"""Async workspace provisioning for camp.

camp ai brings a workspace up in two phases:

  1. Synchronous seed (seed_pending_workspace): create the workspace dir and
     write a manifest listing every member with provision_state="pending". This
     is fast — no git fetch, no worktree add — so the harness launch is not blocked.

  2. Detached provision (spawn_detached_provisioner): spawn `camp setup --background`
     in its own session (start_new_session=True, stdin=DEVNULL, std streams →
     setup.log 0o600) that survives the parent's os.execvp into the harness. The
     assumption (validated) is that this detached child runs to completion after
     the parent process image is replaced — no double-fork needed.

bring_up_workspace ties the two together: seed, then spawn.

The actual per-member work (provision_member) runs the git fetch (under a timeout),
the worktree add, and the bootstrap, then the caller flips the member's manifest
state pending→ready or →failed+reason under the slug-scoped .reconcile.lock. It is
shared by the foreground `camp setup` and the background provisioner —
ONE code path.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..group.manifest import (
    manifest_path_for,
    workspace_dir,
    write_central_manifest,
)

# The camp binary this process was launched as — used to build the detached
# `camp setup --background` argv. Resolved relative to the plugin root
# (tools/camp/plugins/camp/), three levels up from this camp/provision/ module.
_CAMP_BIN = Path(__file__).resolve().parent.parent.parent / "bin" / "camp"


# ---------------------------------------------------------------------------
# Detached spawn
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
    logfile fd is held by the child, so it survives the parent exec.

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

    # Open the logfile 0o600 (security). opener enforces the mode at
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
    from . import reconcile
    from ..group.manifest import read_central_manifest, reconcile_lock

    group_name = group["group"]["name"]
    branch_pattern = group.get("branch_pattern", "worktree-{slug}")
    branch = branch_pattern.format(slug=slug)

    ws_dir = workspace_dir(group_name, slug, env=env)
    mpath = manifest_path_for(group_name, slug, env=env)

    # Serialize the seed against a concurrent `camp remove` teardown of the
    # same slug. The mkdir + manifest write is otherwise unlocked, so it could
    # interleave with reconcile_break's locked rmtree such that the seed survives a
    # remove that already "completed" — a ghost pending workspace. Contend on the
    # SAME slug lock reconcile_break/reconcile_worktree hold (threading + file
    # lock, keyed OUTSIDE ws_dir) so seed and teardown can never overlap.
    with reconcile._slug_lock(f"{group_name}/{slug}"), reconcile_lock(ws_dir):
        ws_dir.mkdir(parents=True, exist_ok=True)

        existing_states: dict[str, dict[str, Any]] = {}
        if mpath.is_file():
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
    profile: Any | None = None,
) -> Path:
    """camp ai bring-up: seed pending members + write workspace docs + spawn provisioner.

    Synchronous steps (fast, before harness launch):
      1. Seed the manifest with every member pending.
      2. Write the workspace doc(s) at the workspace root (idempotent).
      3. Write workspace .claude/settings.json with SessionStart→camp setup --status.
      4. When the inject strategy is "claude-hook", also wire the PostToolUse →
         `camp inject --drain` hook (idempotent); not for "stdout".
      5. When the launch is claude and `[harness] pretrust` is on (default),
         pre-seed the claude per-directory trust flag for the resolved launch cwd
         (claude_trust.pretrust_workspace) so the harness does not stall on the
         trust dialog. Best-effort: a failure is warned and NON-FATAL.
      6. Spawn the detached background provisioner.

    The caller may pass the once-resolved HarnessProfile; otherwise it is resolved
    from group here. Returns the manifest path; the harness launch follows.
    """
    from workspace_doc import write_workspace_doc
    from hooks_writer import write_workspace_hooks, write_workspace_inject_hook
    from harness_profile import resolve_harness_profile

    if profile is None:
        profile = resolve_harness_profile(group)

    group_name = group["group"]["name"]
    mpath = seed_pending_workspace(group, slug, env=env)
    ws_dir = workspace_dir(group_name, slug, env=env)

    write_workspace_doc(ws_dir, group, slug, profile=profile)
    write_workspace_hooks(ws_dir, str(_CAMP_BIN))

    if profile.inject == "claude-hook":
        write_workspace_inject_hook(ws_dir, str(_CAMP_BIN))

    # Pre-seed the claude per-directory trust flag for the resolved launch cwd so
    # the harness does not stall on the trust dialog. Best-effort — the ENTIRE
    # step (gate decision, import, and write) is wrapped so any failure is warned
    # and NON-FATAL: bring-up (manifest seed + detached spawn) still completes.
    try:
        if profile.should_pretrust():
            import claude_trust

            claude_trust.pretrust_workspace(
                profile.resolved_cwd(slug=slug, workspace=ws_dir),
                workspace_root=ws_dir,
                env=env,
            )
    except Exception as exc:  # noqa: BLE001 — best-effort, never blocks bring-up
        print(f"camp: pretrust failed (continuing): {exc}", file=sys.stderr)

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
    from . import reconcile
    from .reconcile import DEFAULT_BASE, FETCH_TIMEOUT_SECONDS

    group_name = group["group"]["name"]
    branch_pattern = group.get("branch_pattern", "worktree-{slug}")
    branch = branch_pattern.format(slug=slug)
    repo_root = Path(member["repo_root"])
    base = member.get("base") or DEFAULT_BASE
    wt_path = workspace_dir(group_name, slug, env=env) / member["name"]

    # Fetch the base ref under a timeout. A TimeoutExpired
    # propagates so an unreachable remote fails the member. A non-timeout fetch
    # failure is fatal only when the base ref does not already resolve locally
    # (raising ReconcileError) — otherwise branching off HEAD would silently put
    # the member on the wrong base; a cached, locally-resolving base proceeds.
    reconcile._fetch_base(repo_root, base, timeout=FETCH_TIMEOUT_SECONDS)
    reconcile._add_worktree_for_member(member, wt_path, branch, repo_root, base=base, slug=slug)
    reconcile._run_bootstrap(member, wt_path)
