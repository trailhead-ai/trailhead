"""Non-blocking member activation for camp.

camp activate <member>:
  (a) Ensures the member is boot-ready — else raises MemberNotReadyError with a
      legible message: 'still provisioning' hint for pending, failure reason
      for failed.
  (b) Marks the member activated IMMEDIATELY and unconditionally — the operator
      gets the member doc and can work in the worktree whether or not its
      activate-phase tasks have finished. Outstanding work-enabling tasks are
      handed to the existing detached provisioner (spawn_detached_provisioner,
      provision/provision.py) rather than run inline, so `camp activate` never
      blocks on an `npm ci` or similar. A required activate-task failure is
      recorded as the member's work_state and does not raise here — activation
      already succeeded by the time any task could fail.
  (c) Prints one feedback line naming what camp actually observed: tasks freshly
      queued (first run, or retrying previously failed work), an activation
      already in progress, work already complete, or a member with no
      activate-phase task declared. Then prints the member's CLAUDE.md to
      stdout so the calling agent ingests it.

Concurrency: a per-(slug, member) lockfile guards against two detached runs for
the same member executing its tasks twice. The lock is released by the OS when
its holder dies (OOM, reboot, laptop sleep), so an interrupted run leaves the
member retryable by the next activation rather than wedged in a sticky
in-progress state. Never unlinked without the lock held; every acquire
re-checks the lockfile's inode, the same discipline the slug-scoped reconcile
lock already uses (group/manifest.py's reconcile_lock / reap_lock_unlocked).

Tasks run shell=False (list-mode, bootstrap-trust posture).
"""

from __future__ import annotations

import fcntl
import os
import sys
from pathlib import Path
from typing import Any

# Activate-phase tasks run wherever the retired dep-install activation hooks ran.
ACTIVATE_PHASE = "activate"


class MemberNotReadyError(Exception):
    """Raised when camp activate is called on a member that is not ready."""


def _find_member_entry(data: dict[str, Any], member_name: str) -> dict[str, Any] | None:
    for m in data.get("members", []):
        if m.get("name") == member_name:
            return m
    return None


def _find_member_config(group: dict[str, Any], member_name: str) -> dict[str, Any] | None:
    for m in group.get("members", []):
        if m.get("name") == member_name:
            return m
    return None


def _member_has_activate_tasks(member_config: dict[str, Any] | None) -> bool:
    if not member_config:
        return False
    return any(
        t.get("phase", "provision") == ACTIVATE_PHASE for t in member_config.get("tasks") or []
    )


def _mark_activated(mpath: Path, member_name: str) -> None:
    """Atomically set activated=True for the named member.

    Unconditional and immediate: activation no longer waits on activate-phase
    tasks, so this write happens before any task has even been dispatched.
    """
    from ..group.manifest import read_central_manifest, reconcile_lock, write_central_manifest

    with reconcile_lock(mpath.parent):
        data = read_central_manifest(mpath)
        for m in data.get("members", []):
            if m.get("name") == member_name:
                m["activated"] = True
                break
        write_central_manifest(mpath, data)


def _persist_activation_result(
    mpath: Path, member_name: str, *, tasks: dict[str, Any], work_state: str
) -> None:
    """Merge a completed activate-phase run's task states + work_state into
    the manifest, under the reconcile lock. Called only by the detached run
    that actually executed the tasks (run_activate_tasks_in_background)."""
    from ..group.manifest import (
        merge_member_tasks,
        read_central_manifest,
        reconcile_lock,
        write_central_manifest,
    )

    with reconcile_lock(mpath.parent):
        data = read_central_manifest(mpath)
        for m in data.get("members", []):
            if m.get("name") == member_name:
                if tasks:
                    merge_member_tasks(m, tasks)
                m["work_state"] = work_state
                break
        write_central_manifest(mpath, data)


# ---------------------------------------------------------------------------
# Per-(slug, member) activate-phase concurrency guard.
#
# A lockfile SIBLING of the workspace dir (never inside it — reconcile_break's
# teardown rmtree must never be able to delete the inode a live holder has
# flocked; mirrors group/manifest.py's lock_path_for reasoning for the slug
# lock). Acquisition is NON-BLOCKING: a losing acquirer never waits, it is told
# "already in progress" (or, for the detached run itself, simply exits without
# running anything — the winning holder already owns the run). Released by an
# explicit unlock on a clean exit, or by the OS closing every fd referencing
# the held open-file-description when the holder process dies — that is the
# crash-safety property this guard exists for.
# ---------------------------------------------------------------------------


def member_guard_lock_path(ws_dir: Path, member_name: str) -> Path:
    """Return the (slug, member) activate-phase guard lockfile path.

    A sibling of ws_dir (central_state_dir(group)/worktrees/<slug>), keyed on
    slug + member — <worktrees-root>/<slug>.<member>.activate.lock — so
    reconcile_break's rmtree of ws_dir can never delete a held lock's inode.
    """
    ws_dir = Path(ws_dir)
    return ws_dir.parent / f"{ws_dir.name}.{member_name}.activate.lock"


def _try_acquire_member_guard(ws_dir: Path, member_name: str):
    """Best-effort non-blocking acquire of the (slug, member) activate guard.

    Returns the open file object (release via _release_member_guard) if
    acquired, or None if another holder already has it, or if this acquire
    lost a race with a concurrent unlink+recreate of the lockfile (detected by
    re-checking the fd's inode against the path's current inode immediately
    after the flock succeeds — the same discipline reconcile_lock uses, and
    what makes reconcile_break's reap of this lockfile race-free: an acquirer
    can never be handed the "same" lock on a different inode).
    """
    lock_path = member_guard_lock_path(ws_dir, member_name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(str(lock_path), "w")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return None

    try:
        path_stat = os.stat(lock_path)
    except FileNotFoundError:
        fd.close()
        return None
    fd_stat = os.fstat(fd.fileno())
    if (path_stat.st_dev, path_stat.st_ino) != (fd_stat.st_dev, fd_stat.st_ino):
        fd.close()
        return None
    return fd


def _release_member_guard(fd) -> None:
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    fd.close()


def _member_guard_free(ws_dir: Path, member_name: str) -> bool:
    """Best-effort probe: True if the guard appears free right now.

    Used only to choose the foreground feedback line ("queued" vs "already in
    progress"). A TOCTOU race against a concurrent activation is possible and
    accepted here — the actual once-only guarantee lives in the detached run's
    own non-blocking acquire (run_activate_tasks_in_background), which is
    always correct even when this probe is stale.
    """
    fd = _try_acquire_member_guard(ws_dir, member_name)
    if fd is None:
        return False
    _release_member_guard(fd)
    return True


def reap_member_guard_unlocked(ws_dir: Path, member_names: list[str]) -> None:
    """Unlink each named member's activate-phase guard lockfile, if any.

    Called by reconcile_break once a slug's workspace dir + manifest are both
    fully torn down (the same point it reaps the slug lock). Never unlinks a
    lockfile without first holding ITS OWN flock (non-blocking): a member whose
    guard is currently held by a live run is skipped rather than force-removed
    — a stale-but-live holder is a rare, later-reapable leak rather than an
    unsafe unlink.
    """
    for name in member_names:
        fd = _try_acquire_member_guard(ws_dir, name)
        if fd is None:
            continue
        try:
            member_guard_lock_path(ws_dir, name).unlink(missing_ok=True)
        finally:
            _release_member_guard(fd)


# ---------------------------------------------------------------------------
# Provisioning notices — camp-authored, templated fields only.
#
# The inject queue is concatenated verbatim into a live agent's context
# through the additionalContext contract, and TaskResult.stderr_excerpt is
# captured unfiltered — so a dependency whose install script prints
# attacker-chosen text to stderr must never reach a notice body. Every notice
# enqueued here is built by build_notice_body from plain strings this module
# controls (member name, phase, failing task name, a canned consequence); no
# TaskResult field is ever passed through.
# ---------------------------------------------------------------------------


def _enqueue_settlement_notice(
    ws_dir: Path, member_name: str, *, task: str | None, consequence: str
) -> None:
    """Enqueue one camp-authored notice for a member reaching a terminal
    activate-phase state (settled ready, or a required task's failure)."""
    from ..launch.inject import build_notice_body, enqueue_notice

    body = build_notice_body(
        member=member_name, phase=ACTIVATE_PHASE, task=task, consequence=consequence
    )
    enqueue_notice(ws_dir, body)


# ---------------------------------------------------------------------------
# Detached-run task execution (the `camp activate <member> --background` body).
# ---------------------------------------------------------------------------


def run_activate_tasks_in_background(
    group: dict[str, Any],
    slug: str,
    member_name: str,
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Run one member's outstanding activate-phase tasks, guarded.

    This is what `camp activate <member> --background` executes — the argv
    spawn_detached_provisioner hands to the detached child. Non-blockingly
    acquires the (slug, member) guard; if another run already holds it, this
    is the losing side of a race between two concurrent `camp activate` calls
    and exits without touching anything (the winning holder already owns the
    run). Otherwise runs the member's activate-phase tasks against the
    manifest's PERSISTED per-task state (run-once-on-success, retried-on-
    failure with cleanup — see provision/tasks.py), then persists the results
    and the member's work_state ("ready" on a clean run, "failed" when a
    required task failed) under the reconcile lock. The guard is released only
    after that persist completes — explicitly on a normal return, or by the OS
    if this process is killed mid-run, in which case the manifest simply keeps
    whatever it last held (never a sticky in-progress state) and the next
    activation's guard acquire finds the lockfile free and retries.
    """
    from ..group.manifest import manifest_path_for, read_central_manifest

    mpath = manifest_path_for(group["group"]["name"], slug, env=env)
    try:
        data = read_central_manifest(mpath)
    except Exception:
        return

    entry = _find_member_entry(data, member_name)
    if entry is None:
        return

    member_config = _find_member_config(group, member_name)
    if not _member_has_activate_tasks(member_config):
        return

    wt_path = Path(entry["worktree_path"])
    ws_dir = wt_path.parent

    fd = _try_acquire_member_guard(ws_dir, member_name)
    if fd is None:
        return
    try:
        from .reconcile import (
            _adapt_task_steps,
            _build_task_context,
            _completed_from_tasks_map,
            _tasks_map_from_results,
            _warn_optional_task_failures,
        )
        from .tasks import TaskError, run_member_tasks

        context = _build_task_context(
            repo_root=member_config["repo_root"],
            worktree=wt_path,
            slug=slug,
            member_name=member_name,
        )
        completed = _completed_from_tasks_map(entry.get("tasks"))

        try:
            results = run_member_tasks(
                _adapt_task_steps(member_config.get("tasks") or []),
                ACTIVATE_PHASE,
                context,
                completed,
            )
        except TaskError as e:
            # A required activate-phase task failed: persist the partial
            # results (including the failing task) and mark work_state
            # "failed" — this member's remaining activate-phase tasks were
            # never reached (run_member_tasks stops at the failure).
            _warn_optional_task_failures(e.results, member_name)
            _persist_activation_result(
                mpath, member_name, tasks=_tasks_map_from_results(e.results), work_state="failed"
            )
            failing_task = e.results[-1].name if e.results else None
            _enqueue_settlement_notice(
                ws_dir,
                member_name,
                task=failing_task,
                consequence=(
                    f"Activate-phase work failed for `{member_name}` — run `camp status` "
                    f"to see which task failed and why."
                ),
            )
            return

        _warn_optional_task_failures(results, member_name)
        _persist_activation_result(
            mpath, member_name, tasks=_tasks_map_from_results(results), work_state="ready"
        )
        _enqueue_settlement_notice(
            ws_dir,
            member_name,
            task=None,
            consequence=(
                f"Activate-phase work finished for `{member_name}` — its dependencies "
                f"and tools are now available."
            ),
        )
    finally:
        _release_member_guard(fd)


def _spawn_background_activation(
    group: dict[str, Any], slug: str, member_name: str, *, env: dict[str, str] | None
) -> None:
    """Hand this member's outstanding activate-phase tasks to a detached run.

    Reuses spawn_detached_provisioner (provision/provision.py) — the same
    non-blocking spawn `camp setup --background` already uses — rather than
    introducing a second async-execution pattern. The child re-invokes this
    same CLI as `camp activate <member> --background`, which runs
    run_activate_tasks_in_background above.
    """
    from ..group.manifest import workspace_dir
    from .provision import _CAMP_BIN, spawn_detached_provisioner

    group_name = group["group"]["name"]
    ws_dir = workspace_dir(group_name, slug, env=env)
    spawn_detached_provisioner(
        group_name=group_name,
        slug=slug,
        logfile_path=str(ws_dir / f"activate-{member_name}.log"),
        _argv=[
            str(_CAMP_BIN),
            "activate",
            member_name,
            "--name",
            slug,
            "--group",
            group_name,
            "--background",
        ],
    )


def _dispatch_activation(
    group: dict[str, Any],
    slug: str,
    member_name: str,
    entry: dict[str, Any],
    member_config: dict[str, Any] | None,
    wt_path: Path,
    *,
    env: dict[str, str] | None,
) -> str:
    """Decide what to do about this member's activate-phase work and return
    the one-line feedback message describing it. Never raises: a required
    task's failure is observed only by a later `camp activate`/`camp status`,
    never here."""
    from ..group.manifest import work_state_for_member

    if not _member_has_activate_tasks(member_config):
        return (
            f"camp activate: {member_name!r} declares no activate-phase task — "
            f"nothing to run."
        )

    work_state = work_state_for_member(entry)
    if work_state == "ready":
        return f"camp activate: {member_name!r} is already work-ready."

    ws_dir = wt_path.parent
    if not _member_guard_free(ws_dir, member_name):
        return (
            f"camp activate: {member_name!r} activation already in progress — "
            f"check `camp status` for updates."
        )

    _spawn_background_activation(group, slug, member_name, env=env)

    if work_state == "failed":
        return (
            f"camp activate: retrying previously failed activate-phase work for "
            f"{member_name!r} in the background."
        )
    return f"camp activate: activate-phase work queued for {member_name!r} in the background."


def _member_doc_content(member_name: str, wt_path: Path) -> str:
    """Return the member's CLAUDE.md content, or a fallback notice."""
    claude_md = wt_path / "CLAUDE.md"
    if claude_md.is_file():
        return claude_md.read_text(encoding="utf-8")
    return (
        f"# {member_name}\n\n"
        f"Member worktree activated: {wt_path}\n"
        f"(No CLAUDE.md found — consider adding one for session context.)\n"
    )


def _surface_member_doc(
    member_name: str,
    wt_path: Path,
    workspace_dir: Path,
    inject: str,
) -> None:
    """Surface the member doc via the resolved inject strategy.

    "stdout"      → print the full doc to stdout (universal floor, unchanged).
    "claude-hook" → only if the PostToolUse `inject --drain` hook is actually
                    installed in <workspace>/.claude/settings.json: enqueue the full
                    doc to the workspace inject queue and print a concise stdout
                    confirmation (the full doc loads on the next turn via the camp
                    PostToolUse hook — NOT dumped to stdout here). If the drain hook
                    is ABSENT, fall back to the stdout floor so the content still
                    reaches the agent (no false "will load via hook" claim).
    """
    doc = _member_doc_content(member_name, wt_path)

    if inject == "claude-hook":
        from ..launch.hooks_writer import has_inject_drain_hook

        if has_inject_drain_hook(workspace_dir):
            from ..launch.inject import enqueue_doc

            enqueue_doc(workspace_dir, doc)
            print(
                f"Entered `{member_name}`; its CLAUDE.md will load into context on the "
                f"next turn via the camp PostToolUse hook."
            )
            return
        print(
            f"camp: claude-hook inject strategy selected but no PostToolUse "
            f"`inject --drain` hook is installed in the workspace — printing "
            f"`{member_name}` CLAUDE.md to stdout instead.",
            file=sys.stderr,
        )

    print(doc, end="")


def activate_member(
    group: dict[str, Any],
    slug: str,
    member_name: str,
    *,
    env: dict[str, str] | None = None,
    profile: Any | None = None,
) -> None:
    """Activate a member for the current session — returns without waiting for
    its activate-phase tasks.

    (a) Checks provision_state: raises MemberNotReadyError for pending or failed.
    (b) Marks the member activated immediately and unconditionally, then hands
        any outstanding activate-phase work to the detached provisioner (never
        runs it inline).
    (c) Prints one feedback line naming what camp observed, then the member's
        CLAUDE.md to stdout.

    Args:
        group:       Parsed group config dict.
        slug:        The workspace slug.
        member_name: The member name to activate.
        env:         Optional env override for state-dir resolution (hermetic tests).

    Raises:
        MemberNotReadyError: If the member is not in the 'ready' state.
        ValueError: If the member name is not found in the manifest.
    """
    from ..group.manifest import manifest_path_for, read_central_manifest

    if profile is None:
        from ..launch.profile import resolve_harness_profile

        profile = resolve_harness_profile(group)

    mpath = manifest_path_for(group["group"]["name"], slug, env=env)
    data = read_central_manifest(mpath)

    entry = _find_member_entry(data, member_name)
    if entry is None:
        raise ValueError(
            f"camp activate: member {member_name!r} not found in manifest for slug {slug!r}"
        )

    state = entry.get("provision_state", "pending")
    if state == "pending":
        raise MemberNotReadyError(
            f"camp activate: member {member_name!r} is still provisioning — "
            f"run `camp status` to check progress or `camp setup` to retry."
        )
    if state == "failed":
        reason = entry.get("reason", "(no reason recorded)")
        raise MemberNotReadyError(
            f"camp activate: member {member_name!r} provisioning failed: {reason}\n"
            f"  Run `camp setup` to retry provisioning."
        )

    wt_path = Path(entry["worktree_path"])
    member_config = _find_member_config(group, member_name)

    if not entry.get("activated", False):
        _mark_activated(mpath, member_name)

    feedback = _dispatch_activation(
        group, slug, member_name, entry, member_config, wt_path, env=env
    )
    print(feedback, file=sys.stderr)

    # The workspace dir is the parent of the member worktree
    # (<workspace>/<member>) — the inject queue lives at <workspace>/.camp/.
    workspace_dir = wt_path.parent
    _surface_member_doc(member_name, wt_path, workspace_dir, profile.inject)
