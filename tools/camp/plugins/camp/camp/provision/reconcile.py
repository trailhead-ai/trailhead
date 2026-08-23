"""Worktree lifecycle reconciler for camp (unified workspace layout).

reconcile_worktree(group, slug):
    Idempotent create-or-reconcile. For each group member:
    - Ensures central_state_dir(group)/worktrees/<slug>/<member> exists on branch
      worktree-<slug>, branched off the member's configured `base` (default
      origin/main). The base ref is only used when it already resolves locally;
      the actual `git fetch` is deferred to the async provisioner, so a
      missing base falls back to HEAD rather than failing synchronous bring-up.
    - Existence-guard before git worktree add (never blindly re-add).
    - Runs each member's provision-phase tasks in parallel (shell=False),
      run-once on success via the manifest's per-member `tasks` state.
    - Writes the central manifest atomically only after ALL members succeed.

reconcile_break(group, slug):
    Removes each member's worktree + the central manifest.
    - Removal confinement: BOTH the manifest-supplied target AND the workspace dir
      (central_state_dir(group)/worktrees/<slug>) are .resolve()'d before the check,
      then the target must be is_relative_to the resolved workspace dir. A
      symlink-escaping worktree_path is rejected. An old-layout path (outside the
      workspace dir) raises a legible legacy-layout error rather than half-applying.
    - Dirty worktree blocks break unless force=True.
    - Break atomicity symmetry: manifest is not left listing a removed member.

A slug-scoped file lock guards concurrent reconcile_worktree AND reconcile_break
calls so two terminals racing camp <slug> don't both git-worktree-add the same
path, and two concurrent `camp remove <slug>` calls don't both pass the
dirty-check and race the manifest write (there is no session lock serializing
removal). The lockfile lives OUTSIDE the workspace dir at
<worktrees-root>/<slug>.lock (manifest.lock_path_for) so reconcile_break's
teardown rmtree of the workspace dir cannot delete the held lock inode.
reconcile_break reaps that lockfile (while still holding the flock) once the
slug is fully torn down; reconcile_lock's inode identity re-check makes the
reap safe for concurrent waiters. It also reaps each member's activate-phase
concurrency guard lockfile (provision/activation.py) at the same point — that
guard is a SEPARATE lock a live `camp activate` run may hold across a long
task subprocess; reconcile_break never waits on it (an in-flight activate run
already keeps its slow work outside this module's reconcile_lock, so teardown
never contends with it) but does clean up its lockfile once free, or leaves it
for a later reap if a run is still (rarely) live at teardown time.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..gitutil import _git, _git_is_dirty
from ..group.manifest import (
    ManifestError,
    manifest_path_for,
    read_central_manifest,
    reap_lock_unlocked,
    reconcile_lock,
    remove_central_manifest,
    workspace_dir,
    write_central_manifest,
)
from .tasks import TaskError, TaskResult, run_member_tasks

# Thread-local lock registry for concurrent-run guard (same process; file lock
# guards cross-process concurrency).
_SLUG_LOCKS: dict[str, threading.Lock] = {}
_SLUG_LOCKS_META = threading.Lock()


class ReconcileError(Exception):
    """Raised on a non-recoverable reconcile failure.

    The message always names the member and the reason.
    """


class ConfinementError(Exception):
    """Raised when a worktree path is outside the resolved workspace dir."""


class LegacyLayoutError(Exception):
    """Raised when a manifest carries an old-layout worktree_path.

    Old layout: <repo_root>/.claude/worktrees/<slug> (outside the unified
    workspace dir). camp cannot safely remove it; the message points the user at
    a manual `git worktree remove`.
    """


# Default branch base for new worktree branches (per-member overridable).
DEFAULT_BASE = "origin/main"

# Per-member git fetch timeout (seconds) for the async provisioner. An
# unreachable remote fails that member instead of hanging the whole bring-up.
FETCH_TIMEOUT_SECONDS = 120

# Consecutive path segments that mark the retired per-repo worktree layout
# (<repo_root>/.claude/worktrees/<slug>). The unified workspace layout never has
# ".claude" immediately followed by "worktrees", so this pair only appears in an
# old-layout manifest path (and won't false-positive on a state dir nested under
# some ".claude" ancestor).
_OLD_LAYOUT_MARKER = (".claude", "worktrees")


def _is_old_layout_path(path: Path) -> bool:
    parts = path.parts
    a, b = _OLD_LAYOUT_MARKER
    return any(parts[i] == a and parts[i + 1] == b for i in range(len(parts) - 1))


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


def _worktree_path(
    group_name: str,
    slug: str,
    member_name: str,
    *,
    env: dict[str, str] | None = None,
) -> Path:
    """Return the member's worktree path under the unified workspace dir:
    central_state_dir(group)/worktrees/<slug>/<member>."""
    return workspace_dir(group_name, slug, env=env) / member_name


def _branch_exists_locally(repo_root: Path, branch: str) -> bool:
    result = _git(repo_root, "branch", "--list", branch)
    return bool(result.stdout.strip())


def _ref_resolves(repo_root: Path, ref: str) -> bool:
    """Return True if `ref` resolves to a commit in repo_root (local clone)."""
    result = _git(repo_root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return result.returncode == 0


def _registered_worktree_paths(repo_root: Path) -> set[Path]:
    """Resolved paths of every worktree registered with git (empty on failure).

    One `git worktree list` read; callers test membership locally instead of
    forking git once per candidate path.
    """
    result = _git(repo_root, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        return set()
    paths: set[Path] = set()
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            registered = line[len("worktree ") :].strip()
            try:
                paths.add(Path(registered).resolve())
            except Exception:
                pass
    return paths


def _worktree_registered(repo_root: Path, wt_path: Path) -> bool:
    """Return True if wt_path is already listed in git's worktree registry."""
    return wt_path.resolve() in _registered_worktree_paths(repo_root)


def _fetch_base(repo_root: Path, base: str, *, timeout: float = FETCH_TIMEOUT_SECONDS) -> None:
    """Fetch the member's base ref under a timeout (async provisioner).

    The base looks like "origin/main"; the remote is the part before the first
    "/". A non-remote base (no "/") is a local ref and skips the fetch. A
    subprocess.TimeoutExpired propagates so the caller fails that member rather
    than hanging on an unreachable remote.

    A non-timeout fetch FAILURE (auth reject, bad URL, ref absent, host down) is
    fatal ONLY when the base ref does not already resolve locally: in that case
    branching off HEAD would silently put the member on the wrong base, so we
    raise ReconcileError (the caller flips the member to failed + reason). If the
    base ref already resolves locally (cached from a prior fetch), a fetch failure
    is non-fatal — proceed with the cached ref.
    """
    remote, _, ref = base.partition("/")
    if not ref:
        return  # local ref — nothing to fetch
    result = subprocess.run(
        ["git", "-C", str(repo_root), "fetch", remote, ref],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0 and not _ref_resolves(repo_root, base):
        raise ReconcileError(
            f"camp: git fetch failed for base {base!r} in {repo_root} and the ref "
            f"does not resolve locally: {result.stderr.strip() or result.stdout.strip()}"
        )


def _move_worktree(member: dict[str, Any], stage: Path, wt_path: Path, repo_root: Path) -> None:
    """git worktree move <stage> <wt_path>, preserving the git-internal admin name.

    On failure, raise ReconcileError but leave <stage> registered so the next
    reconcile resumes at the move step (recovery is idempotent, never
    destructive — see _add_worktree_for_member's state machine).
    """
    result = _git(repo_root, "worktree", "move", str(stage), str(wt_path))
    if result.returncode != 0:
        raise ReconcileError(
            f"camp: git worktree move failed for member {member['name']!r} "
            f"({stage} → {wt_path}): {result.stderr.strip() or result.stdout.strip()}"
        )


def _add_worktree_for_member(
    member: dict[str, Any],
    wt_path: Path,
    branch: str,
    repo_root: Path,
    *,
    base: str = DEFAULT_BASE,
    slug: str,
) -> None:
    """Add a git worktree for one member at wt_path, branched off `base`.

    Admin-name control: git derives a worktree's INTERNAL admin name
    (the dir under `.git/worktrees/<name>`, which Claude Code surfaces as
    `workspace.git_worktree`) from the basename of the add path, de-duplicating
    collisions as `<name>`, `<name>1`, …. Every camp member worktree is leaf-named
    after the MEMBER (`trailhead`), so they all collide and git reports useless
    names like `trailhead5`. To make the admin name the SLUG instead, we
    `git worktree add` at a staging path whose basename IS the slug, then
    `git worktree move` it into the `<member>` folder — `git worktree move`
    preserves the admin name, so the folder keeps the member name while
    `git_worktree` reports the slug.

    Existence-guard: if wt_path already exists (directory present OR registered),
    skip — idempotent re-run.

    Recoverable partial state: the add and move are two phases. The entry is a
    state machine checked IN THIS ORDER, so an interrupt/FS error between the two
    phases is recoverable on the next reconcile rather than bricking the worktree:
      1. wt_path present (dir or registered)           → done, skip.
      2. stage registered (added but not yet moved)    → resume at the MOVE step
         (do NOT re-add — git rejects the duplicate path).
      3. otherwise                                     → fresh add, then move.

    member == slug short-circuit: when stage == wt_path the add path's basename is
    already the slug, so add directly at wt_path with no move (but still add).

    Branch-base policy: a new branch is created off `base` (default origin/main,
    per-member overridable). Because the `git fetch` is deferred to the
    async provisioner, the base ref is only used when it already resolves in the
    local clone; otherwise it falls back to HEAD so synchronous bring-up never
    fails on a not-yet-fetched remote ref.

    Raises ReconcileError on git failure or a confinement violation.
    """
    # One git-registry read; both the existence-guard and the stage-recovery check
    # test membership against it (no second `git worktree list` fork).
    #   NB: paths are matched via Path.resolve(), which FOLLOWS symlinks; on an
    #   overlay/bind-mount FS two distinct paths could resolve equal and cause a
    #   false-positive skip. Acceptable for camp's state-dir layout (no such
    #   aliasing), but noted so a future FS change revisits it.
    registered = _registered_worktree_paths(repo_root)

    # 1. Existence-guard — final path already present (idempotent no-op).
    if wt_path.is_dir() or wt_path.resolve() in registered:
        return

    # Stage is a sibling of wt_path whose basename is the slug (see docstring).
    stage = wt_path.parent / slug

    # Confinement (defense-in-depth): a future caller that bypasses the
    # ^[a-z0-9-]+$ slug validation must not be able to make <stage> escape the
    # workspace via a "../"-laden slug. Mirror reconcile_break's resolve-then-
    # relative_to check. Runs BEFORE any mkdir/git call.
    parent_resolved = wt_path.parent.resolve()
    try:
        stage.resolve().relative_to(parent_resolved)
    except ValueError:
        raise ReconcileError(
            f"camp: refusing worktree add for member {member['name']!r} — stage "
            f"path {stage} escapes the workspace dir {wt_path.parent} "
            f"(slug {slug!r})"
        )

    direct = stage == wt_path  # member == slug → no move needed

    # 2. Partial-state recovery: stage was added but not moved → resume at move.
    if not direct and stage.resolve() in registered:
        _move_worktree(member, stage, wt_path, repo_root)
        return

    # 3. Fresh add (at stage, or directly at wt_path when member == slug).
    add_target = wt_path if direct else stage
    wt_path.parent.mkdir(parents=True, exist_ok=True)

    # An ORPHANED stage dir — present on disk but NOT git-registered (a prior add
    # that failed after creating the dir, or a pruned registry that left the dir) —
    # would make `git worktree add` fail "already exists" on every retry, bricking
    # recovery. Clear it first so the add stays idempotent. (Confined: the assert
    # above guarantees `stage` is under the workspace dir.) Not done for `direct`:
    # an existing wt_path is the step-1 existence-guard's job, not a fresh-add orphan.
    if not direct and stage.exists():
        shutil.rmtree(stage, ignore_errors=True)

    if _branch_exists_locally(repo_root, branch):
        result = _git(repo_root, "worktree", "add", str(add_target), branch)
    else:
        start_point = base if _ref_resolves(repo_root, base) else "HEAD"
        result = _git(repo_root, "worktree", "add", "-b", branch, str(add_target), start_point)

    if result.returncode != 0:
        raise ReconcileError(
            f"camp: git worktree add failed for member {member['name']!r} "
            f"at {add_target}: {result.stderr.strip() or result.stdout.strip()}"
        )

    if not direct:
        _move_worktree(member, stage, wt_path, repo_root)


# ---------------------------------------------------------------------------
# Config-driven task wiring
#
# The task runner (camp.provision.tasks) is a standalone module that consumes
# plain data: it does not know the config-resolved task shape, the manifest, or
# how to print warnings. These helpers adapt between the config-resolved member
# `tasks` list, the manifest's persisted per-task state, and the runner — used
# identically by BOTH provision call paths (provision_member and
# reconcile_worktree phase 2).
# ---------------------------------------------------------------------------

# Provision-phase tasks run wherever the retired single bootstrap command ran.
PROVISION_PHASE = "provision"


def _has_provision_tasks(member: dict[str, Any]) -> bool:
    """True if the member has any provision-phase task to run."""
    return any(
        t.get("phase", PROVISION_PHASE) == PROVISION_PHASE for t in member.get("tasks") or []
    )


# Mirrors activation.ACTIVATE_PHASE, duplicated locally (not imported) so this
# module's only cross-module task-phase dependency stays the same shape as the
# PROVISION_PHASE constant immediately above.
_ACTIVATE_PHASE = "activate"


def _has_activate_tasks(member: dict[str, Any]) -> bool:
    """True if the member declares any activate-phase task in config.

    Used to compute the manifest's work_state fact: a member with no
    activate-phase task at all has nothing to become work-ready FOR, so it
    reports manifest.WORK_STATE_NOT_APPLICABLE rather than sitting at
    "pending" forever waiting for work that will never run.
    """
    return any(t.get("phase", PROVISION_PHASE) == _ACTIVATE_PHASE for t in member.get("tasks") or [])


def _has_outstanding_provision_tasks(
    member: dict[str, Any], tasks_map: dict[str, Any] | None
) -> bool:
    """True if the member has a provision-phase task not recorded "ok".

    Compares the member's config-resolved provision tasks against the manifest's
    persisted per-task state map: a task whose state is "failed" or absent
    (never-run) is outstanding. Used by `camp setup` to decide whether an
    otherwise-ready member still has task work to retry — an all-ok (or task-less)
    member is a true no-op and is not re-provisioned.
    """
    tasks_map = tasks_map or {}
    for task in member.get("tasks") or []:
        if task.get("phase", PROVISION_PHASE) != PROVISION_PHASE:
            continue
        if (tasks_map.get(task["name"]) or {}).get("state") != "ok":
            return True
    return False


def _adapt_task_steps(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adapt config-resolved tasks to the runner's shape.

    The config layer resolves each step as a {"name", "cmd"} table (a legible
    per-step name for reporting); the runner expects each step as a bare argv
    list. Map each task's steps to their `cmd` argv, leaving the rest intact.
    """
    adapted: list[dict[str, Any]] = []
    for task in tasks:
        adapted.append({**task, "steps": [step["cmd"] for step in task.get("steps") or []]})
    return adapted


def _completed_from_tasks_map(
    tasks_map: dict[str, Any] | None, *, over_budget_as_ok: bool = False
) -> dict[str, str]:
    """Project a manifest `tasks` map ({name: {"state": ...}}) onto the runner's
    `completed` shape ({name: state}) so an "ok" task is skipped (run-once).

    `over_budget_as_ok`, when True, also treats a persisted "over-budget" state
    as skip-worthy — the boot-budget-constrained SessionStart hook path
    (reconcile_worktree) must not retry a task that already blew its budget
    within the same tight window. The default (False) leaves "over-budget" as
    its own literal, non-"ok" state, so `camp setup`'s retry path
    (_provision_member_and_flip) re-runs it. Deliberately two directions from
    one persisted fact rather than one shared projection: collapsing them
    would either retry an over-budget task inside the boot budget, or make
    `camp setup` unable to ever retry one.
    """
    out: dict[str, str] = {}
    for name, info in (tasks_map or {}).items():
        state = info.get("state", "")
        if over_budget_as_ok and state == "over-budget":
            state = "ok"
        out[name] = state
    return out


def _tasks_map_from_results(results: list[TaskResult]) -> dict[str, Any]:
    """Project a run's TaskResults onto the persisted manifest `tasks` map.

    A skipped task carries forward its "ok" state (it only skips when already
    ok); a failed task persists its (capped) stderr excerpt as the reason; an
    over-budget task persists verbatim — normalizing it to "ok" would make it
    indistinguishable from success and it would never be retried anywhere.
    """
    out: dict[str, Any] = {}
    for result in results:
        if result.state == "failed":
            entry: dict[str, Any] = {"state": "failed"}
            if result.stderr_excerpt:
                entry["reason"] = result.stderr_excerpt
            out[result.name] = entry
        elif result.state == "over-budget":
            out[result.name] = {"state": "over-budget"}
        else:  # "ok" or "skipped" (skipped means already ok)
            out[result.name] = {"state": "ok"}
    return out


def _warn_optional_task_failures(results: list[TaskResult], member_name: str) -> None:
    """Print a one-line stderr warning for each optional task that failed.

    A required-task failure raises TaskError instead of returning, so any failed
    result reaching here is an optional task whose failure the run tolerated —
    it must still be visible, or a member reads "ready" while a downstream tool
    silently breaks. Matches the stderr-warning convention used elsewhere.
    """
    for result in results:
        if result.state == "failed":
            print(
                f"camp: optional task {result.name!r} failed for member "
                f"{member_name!r} — run `camp status` for details.",
                file=sys.stderr,
            )


def _build_task_context(
    *, repo_root: Path | str, worktree: Path, slug: str, member_name: str
) -> dict[str, Any]:
    """Build the {placeholder} substitution + cwd context for the task runner.

    Carries the placeholders a task recipe may reference (repo_root, worktree,
    workspace, slug); `worktree` is also the subprocess cwd, and `member` names
    the member in error/warning messages. The workspace dir is the worktree's
    parent (<workspace>/<member>).
    """
    return {
        "repo_root": repo_root,
        "worktree": worktree,
        "workspace": worktree.parent,
        "slug": slug,
        "member": member_name,
    }


def _remove_worktree_for_member(
    member: dict[str, Any],
    wt_path: Path,
    repo_root: Path,
    workspace_dir: Path,
    *,
    force: bool,
) -> None:
    """Remove a git worktree for one member.

    Confinement: BOTH wt_path and workspace_dir are .resolve()'d before the check,
    then wt_path must be is_relative_to the resolved workspace_dir — so a
    symlink-escaping worktree_path is rejected.

    Raises ConfinementError if the path escapes the workspace dir.
    Raises ReconcileError if git worktree remove fails.
    """
    try:
        wt_resolved = wt_path.resolve()
        ws_resolved = workspace_dir.resolve()
        wt_resolved.relative_to(ws_resolved)
    except ValueError:
        raise ConfinementError(
            f"camp: worktree path {wt_path} resolves outside the workspace dir "
            f"{workspace_dir} for member {member['name']!r} — refusing removal"
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
      2. Runs each member's provision-phase tasks (shell=False), run-once on
         success via the manifest's per-member `tasks` state.
    Then writes the central manifest atomically.

    Concurrent-run guard: a per-slug lock (threading + file lock) prevents two
    concurrent reconcile_worktree calls from both running git worktree add.

    A required task's failure raises ReconcileError and writes no manifest
    (atomicity — never a half-provisioned member set). An optional task's failure
    is recorded in the member's manifest `tasks` map, warned on stderr, and does
    not block the manifest write.

    Each member's manifest entry is rebuilt from scratch every run, so any prior
    `provision_state`/`activated`/`reason` (set elsewhere, by cmd_setup_group /
    activation.py) is read up front and copied onto the rebuilt entry unchanged —
    reconcile_worktree never sets these fields itself, so this is a pure
    carry-forward. Without it, this function (invoked on every SessionStart) would
    silently wipe them.

    Returns a result dict with:
        member_count:  int
        members:       list of member names
        manifest_path: str
        tasks:         "ok"

    Raises:
        ReconcileError: on git failure or a required-task failure (member named).
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
        with reconcile_lock(mpath.parent):
            # -- Phase 1: Create member worktrees (existence-guarded)
            member_results: list[dict[str, Any]] = []
            for member in members:
                repo_root = Path(member["repo_root"])
                wt_path = _worktree_path(group_name, slug, member["name"], env=env)
                base = member.get("base") or DEFAULT_BASE
                _add_worktree_for_member(member, wt_path, branch, repo_root, base=base, slug=slug)
                member_results.append(
                    {
                        "name": member["name"],
                        "repo_root": str(repo_root),
                        "worktree_path": str(wt_path),
                    }
                )

            # Prior per-member task states (run-once): a task recorded "ok" in a
            # prior manifest is skipped this run. Absent on the first reconcile.
            #
            # prior_state carries forward provision_state/activated/reason/
            # work_state set by cmd_setup_group/activation.py — reconcile_worktree
            # never sets provision_state/activated/reason itself (it does set
            # work_state — see the not-applicable default below), so this is a
            # pure carry-forward (not a merge with new values) for the other
            # three. A member with no prior entry gets no key, same as today.
            prior_tasks: dict[str, dict[str, Any]] = {}
            prior_state: dict[str, dict[str, Any]] = {}
            if mpath.is_file():
                try:
                    for m in read_central_manifest(mpath).get("members", []):
                        prior_tasks[m["name"]] = m.get("tasks") or {}
                        prior_state[m["name"]] = {
                            key: m[key]
                            for key in ("provision_state", "activated", "reason", "work_state")
                            if key in m
                        }
                except ManifestError:
                    prior_tasks = {}
                    prior_state = {}

            # -- Phase 2: Run provision-phase tasks per member in parallel.
            #
            # A task this member's prior manifest recorded "over-budget" is
            # filtered out of the submitted list entirely (skip-worthy on this
            # boot-budget-constrained hook path) rather than left for
            # run_member_tasks's own "ok" skip to handle: that path returns a
            # "skipped" TaskResult, and _tasks_map_from_results always persists
            # "skipped" as "ok" — which would silently erase the over-budget
            # record. Pre-filtering means no TaskResult is produced for it at
            # all, so the merge below leaves the prior "over-budget" entry
            # untouched. `camp setup`'s retry path (_provision_member_and_flip)
            # takes no such filter — over-budget stays retry-worthy there.
            task_results: dict[str, list[TaskResult]] = {}
            required_failure: Exception | None = None
            if any(_has_provision_tasks(m) for m in members):
                with ThreadPoolExecutor(max_workers=len(members)) as executor:
                    futures = {}
                    for member, mr in zip(members, member_results):
                        context = _build_task_context(
                            repo_root=mr["repo_root"],
                            worktree=Path(mr["worktree_path"]),
                            slug=slug,
                            member_name=member["name"],
                        )
                        completed = _completed_from_tasks_map(
                            prior_tasks.get(member["name"]), over_budget_as_ok=True
                        )
                        runnable_tasks = [
                            t
                            for t in _adapt_task_steps(member.get("tasks") or [])
                            if completed.get(t["name"]) != "ok"
                        ]
                        fut = executor.submit(
                            run_member_tasks,
                            runnable_tasks,
                            PROVISION_PHASE,
                            context,
                            completed,
                        )
                        futures[fut] = member["name"]

                    for fut in as_completed(futures):
                        name = futures[fut]
                        try:
                            task_results[name] = fut.result()
                        except TaskError as e:
                            # A required task failed — surface it as ReconcileError
                            # so the caller (and the session hook) treats it exactly
                            # as a bootstrap failure did: no manifest is written.
                            required_failure = ReconcileError(str(e))
                            break
                        except Exception as e:
                            required_failure = e
                            break

            if required_failure is not None:
                raise required_failure

            # Optional-task failures: warn on stderr and persist their state, but
            # do not block the manifest write. Merge each member's results over
            # its prior task map so states from other phases/runs survive.
            for member, mr in zip(members, member_results):
                results = task_results.get(member["name"], [])
                _warn_optional_task_failures(results, member["name"])
                merged = dict(prior_tasks.get(member["name"]) or {})
                merged.update(_tasks_map_from_results(results))
                if merged:
                    mr["tasks"] = merged
                mr.update(prior_state.get(member["name"], {}))
                # A member with no activate-phase task declared has no work to
                # ever become work-ready FOR — set that explicitly rather than
                # leaving work_state absent (which reads as "pending" forever,
                # per manifest.work_state_for_member). A prior work_state
                # already carried forward above takes precedence.
                if "work_state" not in mr and not _has_activate_tasks(member):
                    mr["work_state"] = "not-applicable"

            # -- Phase 3: Write central manifest atomically (only after all succeed)
            manifest_data: dict[str, Any] = {
                "schema_version": 1,
                "group": group_name,
                "slug": slug,
                "branch": branch,
                "members": member_results,
            }
            write_central_manifest(mpath, manifest_data)

    return {
        "slug": slug,
        "member_count": len(member_results),
        "members": [mr["name"] for mr in member_results],
        "manifest_path": str(mpath),
        "tasks": "ok",
    }


def reconcile_break(
    group: dict[str, Any],
    slug: str,
    *,
    env: dict[str, str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Remove a worktree set for (group, slug).

    Concurrency: with no session lock, two concurrent
    `camp remove <same-slug>` calls could both pass the dirty-check and race the
    manifest write. reconcile_break acquires the slug-scoped reconcile lock (the
    SAME lock reconcile_worktree, seed_pending_workspace, and the provisioner
    flips hold) so removal of a given slug serializes across processes/threads.
    That lockfile lives OUTSIDE the workspace dir, so the teardown rmtree
    below cannot delete the inode this call is holding. The lock is taken AFTER a
    fail-fast manifest read so a nonexistent slug raises ManifestError without
    even creating the stray lockfile.

    Algorithm:
      1. Read the central manifest to get member worktree paths.
      2. Confinement pre-check (BEFORE touching any path): each
         worktree_path must resolve inside the resolved workspace dir
         (central_state_dir(group)/worktrees/<slug>). A symlink-escaping path is
         rejected (ConfinementError); an old-layout path under a repo_root is
         rejected with a legible LegacyLayoutError. This runs FIRST so the
         dirty-check's `git -C <worktree_path>` never executes in an unconfined
         cwd. The pre-check aborts the whole break — never a half-applied removal.
      3. Check all (now-confined) members for dirty trees (abort unless force=True).
      4. Remove each member worktree via git worktree remove.
      5. Remove the central manifest ONLY if all removals succeeded (break
         atomicity symmetry — never leave a manifest listing a removed member).
      6. On full teardown, reap the slug lockfile while still holding its flock
         (reap_lock_unlocked) — a partial removal keeps both the manifest and
         the lockfile, since the slug is still live.

    Returns a result dict with status, removed members, and any errors.

    Raises:
        ManifestError: If the manifest is malformed.
        ConfinementError: If a worktree path resolves outside the workspace dir.
        LegacyLayoutError: If a worktree path uses the retired per-repo layout.
        ReconcileError: If a member worktree is dirty and force=False.
    """
    group_name: str = group["group"]["name"]
    mpath = manifest_path_for(group_name, slug, env=env)
    ws_dir = workspace_dir(group_name, slug, env=env)
    ws_resolved = ws_dir.resolve()

    # Fail-fast on a nonexistent slug BEFORE taking the reconcile lock. The lock
    # lives at <worktrees-root>/<slug>.lock (outside ws_dir), so acquiring
    # it does not pre-create the workspace dir; but it WOULD create a stray
    # <slug>.lock file for a slug that never existed. A bare existence check gates
    # that — the full parse is wasted here since the authoritative read happens
    # under the lock below.
    if not mpath.exists():
        raise ManifestError(f"camp: cannot read manifest at {mpath}: no such workspace")

    # Serialize removal of this slug. The slug-scoped reconcile lock (the same
    # .reconcile.lock reconcile_worktree holds) closes the TOCTOU window where
    # two concurrent `camp remove <slug>` calls both pass the dirty-check and
    # race the manifest write when there is no session lock.
    lock = _slug_lock(f"{group_name}/{slug}")
    with lock, reconcile_lock(mpath.parent):
        # Authoritative read under the lock (a concurrent break may have already
        # removed it — ManifestError then surfaces cleanly). In that case our
        # own acquire just re-created the lockfile for a slug that no longer
        # exists — reap it before surfacing, or it leaks forever.
        try:
            manifest_data = read_central_manifest(mpath)
        except ManifestError:
            if not mpath.exists() and not ws_dir.exists():
                reap_lock_unlocked(ws_dir)
            raise
        member_entries = manifest_data.get("members", [])

        # Confinement pre-check FIRST: validate all paths BEFORE touching any of
        # them. This MUST precede the dirty-check — the dirty-check runs
        # `git -C <worktree_path> status`, so a tampered/legacy manifest whose
        # worktree_path escapes the workspace would otherwise exec git in an
        # unconfined cwd before the guard refuses removal.
        for entry in member_entries:
            wt_path = Path(entry["worktree_path"])
            try:
                wt_path.resolve().relative_to(ws_resolved)
                continue  # inside the workspace dir — OK
            except ValueError:
                pass

            # Outside the workspace dir. Distinguish a retired per-repo layout
            # path (legible legacy error) from an arbitrary/symlink-escaping path.
            if _is_old_layout_path(wt_path):
                raise LegacyLayoutError(
                    f"camp: member {entry['name']!r} worktree_path {wt_path} uses the "
                    f"retired per-repo layout (outside the workspace dir {ws_dir}). "
                    f"camp will not remove it — run `git worktree remove {wt_path}` manually."
                )
            raise ConfinementError(
                f"camp: worktree path {wt_path} resolves outside the workspace dir "
                f"{ws_dir} for member {entry['name']!r} — refusing removal"
            )

        # Dirty-check (only after every path is confined — see above).
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
                _remove_worktree_for_member(member, wt_path, repo_root, ws_dir, force=force)
                removed.append(name)
            except (ConfinementError, LegacyLayoutError):
                raise
            except Exception as e:
                errors.append(f"{name}: {e}")

        # Break atomicity symmetry: only remove the manifest if all removals
        # succeeded (or if the members that failed were already absent).
        # Do NOT leave a manifest listing members whose worktrees are removed.
        if not errors:
            remove_central_manifest(mpath)
            # Remove the camp-owned workspace dir itself (.camp, .claude,
            # setup.log, doc files). Leaving it behind would make the next
            # `camp new <slug>` re-enter a torn-down workspace; that re-enter
            # decision keys on MANIFEST presence (removed just above),
            # so a stale ws_dir alone does not trigger a wrong re-enter, but we
            # still remove it to leave no orphan state. The slug lock lives
            # OUTSIDE ws_dir, so this rmtree never deletes the held lock.
            # Confinement: the resolved workspace dir MUST
            # sit under the resolved worktrees root (central_state_dir/worktrees)
            # anchored independently of ws_dir — never rmtree an unconfined path
            # (symlink escape, old layout, etc.).
            if ws_dir.exists():
                from ..group.resolve import central_state_dir

                worktrees_root = (central_state_dir(group_name, env=env) / "worktrees").resolve()
                try:
                    ws_resolved.relative_to(worktrees_root)
                except ValueError:
                    raise ConfinementError(
                        f"camp: workspace dir {ws_dir} resolves outside the worktrees "
                        f"root {worktrees_root} — refusing removal"
                    )
                shutil.rmtree(ws_resolved)
            # Slug fully torn down (manifest and workspace dir both gone) —
            # reap the slug lockfile while STILL HOLDING its flock. Waiters
            # blocked on this inode re-validate identity on wake (see
            # reconcile_lock), which is what makes the unlink race-free.
            reap_lock_unlocked(ws_dir)
            # Also reap each member's activate-phase concurrency guard
            # lockfile (provision/activation.py). Without this, every removed
            # workspace that ever ran activate-phase work leaks one lockfile
            # per member, permanently — that guard lives OUTSIDE ws_dir for
            # the same reason the slug lock does, so the rmtree above never
            # touched it.
            from .activation import reap_member_guard_unlocked

            reap_member_guard_unlocked(ws_dir, [e["name"] for e in member_entries])
            status = "ok"
        else:
            # Some removals failed. Update the manifest to reflect reality:
            # remove entries for members that were successfully removed so the
            # manifest never lists a member whose worktree is gone.
            remaining = [e for e in member_entries if e["name"] not in removed]
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
