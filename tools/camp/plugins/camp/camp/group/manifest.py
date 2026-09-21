"""Central manifest read/write/remove for camp.

The central manifest lives at:
    central_state_dir(group)/worktrees/<slug>/manifest.json

It is written atomically (temp file + os.replace) with mode 0o600.
A malformed or truncated manifest raises ManifestError, not a raw traceback.

Schema (v1):
    {
        "schema_version": 1,
        "group": "<group-name>",
        "slug": "<worktree-slug>",
        "branch": "worktree-<slug>",
        "members": [
            {
                "name": "<repo-name>",
                "repo_root": "/absolute/path/to/canonical/repo",
                # Unified workspace layout:
                #   central_state_dir(group)/worktrees/<slug>/<name>
                "worktree_path": "/abs/.../worktrees/<slug>/<name>",
                # Async provisioning state: "pending" | "ready" | "failed".
                # Seeded "pending" by camp ai; flipped by the (foreground or
                # background) provisioner. Boot-readiness ONLY — whether the
                # worktree is materialized and cheap provision-phase work is
                # done. A "failed" member also carries "reason".
                "provision_state": "pending",
                # Work-readiness: a sibling fact, added additively alongside
                # provision_state and never replacing it. Same domain plus
                # "not-applicable" (a member that declares no activate-phase
                # task). Absent on a manifest written before this key existed —
                # read it via manifest.work_state_for_member, which defaults a
                # missing key to "pending" (not-yet-work-ready) rather than
                # raising; there is no migration step.
                "work_state": "pending",
                "reason": "<failure reason — present only when failed>",
                # Per-task run-once state, keyed by task name. Written by the
                # provision/reconcile task runner; a task recorded "ok" is never
                # re-run. Present only once a member has run tasks.
                "tasks": {"<task-name>": {"state": "ok"}},
            },
            ...
        ],
        # The declared host name of the machine that created this workspace,
        # stamped at seed time from that host's own hosts.toml self_name.
        # Absent on a manifest whose creating host declared no self_name, and
        # on every manifest written before this key existed — read it via
        # manifest.owner_of, which defaults a missing key to None ("never
        # recorded") rather than raising; there is no migration step. None
        # never means "owned by this host" and never triggers a rewrite.
        # Every writer in this module passes through write_central_manifest,
        # which refuses to drop or change an on-disk owner unless the caller
        # passes allow_owner_change=True — a rebuild site that forgets to
        # carry an owner forward fails loudly on its first write instead of
        # shipping silently.
        "owner": "<declared host name, or absent>",
    }
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class ManifestError(Exception):
    """Raised when a central manifest file is malformed or cannot be read.

    The message always includes the file path and the failing reason.
    """


class LockTimeout(Exception):
    """Raised by `reconcile_lock` when a bounded `timeout` expires before the
    lock could be acquired — held by another process or thread.

    Never raised by the default, unbounded acquire (`timeout=None`).
    """


#: The poll interval a bounded `reconcile_lock` acquire sleeps between
#: `LOCK_NB` attempts. Short relative to any bound a caller would pass, so a
#: caller's own timeout dominates how long a bounded acquire can take.
_LOCK_POLL_INTERVAL_SECONDS = 0.05


def write_central_manifest(path: Path, data: dict[str, Any], *, allow_owner_change: bool = False) -> None:
    """Write data to path atomically with mode 0o600.

    Uses a temp file in the same directory + os.replace for atomicity.
    Sets file mode to 0o600 after the write (umask-proof).

    Bypass-proof ownership guard: every manifest write in the plugin passes
    through this one function, so it is the single place that can refuse a
    write that would silently drop a recorded owner. Before writing, if a
    manifest already exists at `path` and carries a string "owner", the
    incoming `data` must carry that SAME owner value, or the write is
    refused with a named ManifestError — unless the caller passes
    `allow_owner_change=True`, the explicit opt-in a deliberate ownership
    change (or clear) uses. A path with no on-disk manifest, or an on-disk
    manifest carrying no owner, has nothing to protect and every write is
    accepted unconditionally, opt-in or not — this guard is invisible on the
    hot path of an ownerless workspace.

    An on-disk manifest whose "owner" is present but not a string is refused
    the same way as a would-be drop, rather than treated as "nothing to
    protect" — that shape is exactly the case this guard exists to catch,
    and letting it read as ownerless would silently disable the guard for
    the one write that most needs it. `allow_owner_change=True` still opts
    out, same as every other refusal here.

    An on-disk manifest that cannot even be read (malformed or truncated
    JSON) is different: there is no owner to protect because there is
    nothing to read, so this is treated as ownerless rather than refused —
    refusing here would block every writer (including reconcile's own
    self-heal) on damage the write is trying to repair, with no
    `allow_owner_change` surface most callers can reach to recover. The
    write proceeds, but never silently: a diagnostic naming the path and
    the read failure goes to stderr first, so an operator sees that
    whatever owner the corrupt file may have carried was not verified.

    Args:
        path:  Absolute path for the manifest file (parent must exist).
        data:  Dict to serialize as JSON.
        allow_owner_change: Opt in to writing a value that changes or drops
            a known on-disk owner (a string "owner" successfully read, or a
            non-string "owner" value). Defaults to False. Has no bearing on
            an unreadable on-disk manifest, which is never refused.

    Raises:
        ManifestError: If the write would drop or change a known on-disk
            owner, or the on-disk owner is present but not a string — none
            without `allow_owner_change=True`.
    """
    if not allow_owner_change and path.is_file():
        try:
            on_disk: dict[str, Any] | None = read_central_manifest(path)
        except ManifestError as e:
            print(
                f"camp: warning: on-disk manifest at {path} could not be "
                f"read ({e}) — overwriting it; any owner it may have "
                f"recorded could not be verified",
                file=sys.stderr,
            )
            on_disk = None
        if on_disk is not None:
            disk_owner = on_disk.get("owner")
            if disk_owner is not None:
                if not isinstance(disk_owner, str):
                    raise ManifestError(
                        f"camp: refusing to write manifest at {path}: the "
                        f"on-disk owner is not a string (got "
                        f"{type(disk_owner).__name__}) — pass "
                        f"allow_owner_change=True for a deliberate ownership "
                        f"change"
                    )
                if data.get("owner") != disk_owner:
                    raise ManifestError(
                        f"camp: refusing to write manifest at {path}: this write "
                        f"would drop or change the recorded owner {disk_owner!r} "
                        f"— pass allow_owner_change=True for a deliberate "
                        f"ownership change"
                    )

    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".manifest-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, str(path))
        os.chmod(str(path), 0o600)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_central_manifest(path: Path) -> dict[str, Any]:
    """Read and parse the central manifest at path.

    Args:
        path:  Absolute path to the manifest file.

    Returns:
        Parsed dict.

    Raises:
        ManifestError: If the file is missing, unreadable, or contains malformed JSON.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ManifestError(f"camp: cannot read manifest at {path}: {e}") from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ManifestError(f"camp: malformed manifest at {path}: {e}") from e

    if not isinstance(data, dict):
        raise ManifestError(f"camp: manifest at {path} is not a JSON object (got {type(data).__name__})")

    return data


def remove_central_manifest(path: Path) -> None:
    """Remove the central manifest file if it exists.

    Silently succeeds if the file is already gone.

    Args:
        path:  Absolute path to the manifest file.
    """
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def lock_path_for(ws_dir: Path) -> Path:
    """Return the slug-scoped lockfile path for a workspace dir.

    The lockfile is a SIBLING of the workspace dir — <worktrees-root>/<slug>.lock,
    NOT a file inside it. This is load-bearing for cross-process mutual exclusion:
    reconcile_break's teardown `shutil.rmtree`'s the entire workspace dir, so a
    lockfile held INSIDE it would have its inode deleted mid-critical-section. A
    concurrent acquirer (blocked on the old inode, or arriving in the
    rmtree→release window) would then mkdir the dir + flock a brand-NEW inode and
    get zero mutual exclusion. Keying the lock on the persistent worktrees
    root closes that window — the inode the holder flocks is never the one rmtree
    removes.

    ws_dir is central_state_dir(group)/worktrees/<slug>, so ws_dir.name is the
    slug and ws_dir.parent is the worktrees root.

    Lifecycle: created on first acquire (reconcile_lock), reaped by
    reconcile_break (reap_lock_unlocked) once the slug is fully torn down — a
    lockfile with no live slug is a leak, not a fixture.
    """
    ws_dir = Path(ws_dir)
    return ws_dir.parent / f"{ws_dir.name}.lock"


def _acquire_flock(lock_fd, lock_path: Path, *, deadline: float | None) -> None:
    """Take the exclusive flock on *lock_fd*: unbounded when *deadline* is
    `None` (what the provisioner and every other manifest writer relies on),
    or polled `LOCK_NB` until free or the monotonic *deadline* has passed, at
    which point `LockTimeout` is raised instead of blocking forever. The
    deadline is the caller's, so a retry after a reaped inode spends what is
    left of the same budget rather than starting a fresh one."""
    if deadline is None:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        return
    while True:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LockTimeout(f"camp: lock at {lock_path} is held by another camp process")
            time.sleep(_LOCK_POLL_INTERVAL_SECONDS)


@contextmanager
def reconcile_lock(ws_dir: Path, *, timeout: float | None = None):
    """Acquire the slug-scoped lock guarding manifest mutations.

    All status flips (background provisioner + foreground `camp setup`),
    reconcile_worktree's create, reconcile_break's teardown, and
    seed_pending_workspace's seed serialize on this lock so concurrent writers
    never tear the whole-manifest temp+rename write AND a `camp new` seed never
    races a `camp remove` teardown into a ghost workspace.

    The lockfile lives OUTSIDE ws_dir at <worktrees-root>/<slug>.lock (see
    lock_path_for) so reconcile_break's rmtree of ws_dir cannot delete the held
    lock inode. Only the worktrees root is mkdir'd here — locking a slug
    never pre-creates that slug's workspace dir.

    Acquisition validates inode identity: reconcile_break reaps the lockfile
    (via reap_lock_unlocked, while still holding the flock) once a slug is fully
    torn down. A waiter that blocked on the reaped inode wakes up holding a lock
    no newcomer can see — zero exclusion — so after every flock we re-check that
    the inode we hold is still the inode at lock_path, and retry on the current
    file if not. This is what makes the reap safe.

    *timeout*, when given, bounds acquisition (see :func:`_acquire_flock`) and
    raises `LockTimeout` on expiry rather than blocking forever — the mode
    `reconcile_workspace_record` uses so a `camp attach`/`camp stop` caught
    behind the provisioner's own long-held lock reports "not reconciled"
    instead of hanging silently. `None` (the default) keeps every other
    caller's unbounded wait unchanged.
    """
    lock_path = lock_path_for(ws_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        lock_fd = open(str(lock_path), "w")
        try:
            _acquire_flock(lock_fd, lock_path, deadline=deadline)
            try:
                path_stat = os.stat(lock_path)
            except FileNotFoundError:
                # Reaped while we blocked — the fd guards an orphaned inode.
                lock_fd.close()
                continue
            fd_stat = os.fstat(lock_fd.fileno())
            if (path_stat.st_dev, path_stat.st_ino) != (fd_stat.st_dev, fd_stat.st_ino):
                # Reaped and re-created — same orphan problem, fresh file wins.
                lock_fd.close()
                continue
        except BaseException:
            lock_fd.close()
            raise
        break
    try:
        yield
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fd.close()


def reap_lock_unlocked(ws_dir: Path) -> None:
    """Unlink the slug-scoped lockfile. The caller MUST hold its flock.

    Unlinking while HOLDING the exclusive flock is what makes removal safe: a
    waiter blocked on the unlinked inode wakes, fails reconcile_lock's inode
    identity re-check, and retries on the current file — it can never proceed
    on the orphaned inode. Unlinking without the lock held would hand two
    processes the "same" lock on different inodes.

    Silently succeeds if the lockfile is already gone.
    """
    lock_path_for(ws_dir).unlink(missing_ok=True)


WORK_STATE_NOT_APPLICABLE = "not-applicable"


def work_state_for_member(member: dict[str, Any]) -> str:
    """Return a member's work-readiness fact.

    Domain: "pending" | "ready" | "failed" | WORK_STATE_NOT_APPLICABLE (a
    member that declares no activate-phase task). A manifest written before
    this key existed carries no "work_state" entry at all — that reads as
    "pending" (not-yet-work-ready) rather than raising, so no migration step
    is required.
    """
    return member.get("work_state", "pending")


def work_state_for_new_entry(
    member: dict[str, Any] | None, prior: dict[str, Any] | None
) -> str | None:
    """The `work_state` a freshly built manifest entry should carry, or None.

    The single rule "a member with no activate-phase task has no work to ever
    become work-ready for" lives here, beside `work_state_for_member`, so both
    writers of a fresh member entry — `reconcile_worktree`'s Phase 2 merge and
    `seed_pending_workspace`'s entry build (including its idempotent re-seed of
    an existing workspace) — apply it identically instead of each inlining
    its own copy.

    Precedence: a *prior* entry that already carries a "work_state" key wins
    outright (carry-forward — this rebuild never overwrites a value another
    writer, e.g. activation.py, already recorded). Only when there is no prior
    value does the "no activate-phase task" rule apply, per `tasks_in_phase`.
    Returns None when neither applies, so the caller writes no "work_state"
    key at all — the same "absent reads as pending" posture
    `work_state_for_member` already documents.
    """
    from .config import tasks_in_phase

    if prior is not None and "work_state" in prior:
        return prior["work_state"]
    if not tasks_in_phase(member, "activate"):
        return WORK_STATE_NOT_APPLICABLE
    return None


def owner_of(manifest: dict[str, Any]) -> str | None:
    """Return the workspace's declared owning host, or None if never recorded.

    A manifest written before this key existed, or written by a host that
    declared no self_name, carries no "owner" entry at all — that reads as
    None ("never recorded"), never as "owned by this host", and reading it
    never triggers a rewrite. There is no migration step.

    Raises:
        ManifestError: If "owner" is present but not a string — refused
            rather than silently coerced, since ownership is a single
            declared host, not a value to guess at.
    """
    owner = manifest.get("owner")
    if owner is None:
        return None
    if not isinstance(owner, str):
        raise ManifestError(f"camp: manifest owner must be a string (got {type(owner).__name__})")
    return owner


def carry_forward_owner(manifest_data: dict[str, Any], prior_owner: str | None) -> None:
    """Preserve *prior_owner* into *manifest_data* ahead of a manifest rebuild.

    Pure carry-forward: a rebuild never sets or clears ownership itself, only
    preserves whatever was already recorded. When *prior_owner* is None (the
    prior manifest never recorded one), no "owner" key is added at all — not
    even as None. An "owner" the caller already placed in *manifest_data*
    before calling this is never overwritten by a stale prior value.

    Mutates *manifest_data* in place; callers assemble the rest of the
    rebuilt manifest around this call.
    """
    if prior_owner is None:
        return
    manifest_data.setdefault("owner", prior_owner)


def merge_member_tasks(member: dict[str, Any], tasks: dict[str, Any]) -> None:
    """Merge a per-task state map into `member["tasks"]` in place.

    Shared by every manifest write that records task outcomes
    (`flip_member_state_unlocked` for provision-phase, `activation._mark_activated`
    for activate-phase) so persisting one phase's results never clobbers task
    states recorded by another phase or a prior run.
    """
    merged = member.get("tasks", {})
    merged.update(tasks)
    member["tasks"] = merged


def flip_member_state_unlocked(
    path: Path,
    member_name: str,
    state: str,
    *,
    reason: str | None = None,
    tasks: dict[str, Any] | None = None,
) -> None:
    """Read-mutate-write one member's provision_state WITHOUT acquiring the lock.

    The caller MUST already hold the .reconcile.lock (re-acquiring flock on a
    second fd in the same process deadlocks) — wrap the call in
    `with reconcile_lock(path.parent): ...`. Production flips run inside the
    reconcile_lock already held by the provisioner, so this unlocked primitive is
    the only flip path.

    When `tasks` is given, it is a per-task state map ({name: {"state": ...}})
    that is MERGED into the member's existing `tasks` map in the same write, so
    persisting a provision run's task outcomes never clobbers task states
    recorded by another phase or a prior run.
    """
    data = read_central_manifest(path)
    for member in data.get("members", []):
        if member.get("name") == member_name:
            member["provision_state"] = state
            if state == "failed" and reason is not None:
                member["reason"] = reason
            elif state != "failed":
                member.pop("reason", None)
            if tasks:
                merge_member_tasks(member, tasks)
            break
    write_central_manifest(path, data)


def workspace_dir(group: str, slug: str, *, env: dict[str, str] | None = None) -> Path:
    """Return the unified workspace dir for (group, slug).

    The single source of truth for central_state_dir(group)/worktrees/<slug>;
    manifest_path_for and the provision/reconcile/shell-integration callers all
    derive their paths from this.

    Args:
        group:  Group name (validated by central_state_dir).
        slug:   Worktree slug.
        env:    Optional env override for the resolver (hermetic tests).

    Returns:
        Absolute path to the workspace dir (directory may not exist yet).

    Raises:
        GroupConfinementError: If group or slug fails path-confinement
            validation — slug is validated here (not just at the CLI layer)
            because callers routinely re-derive it from a STORED record
            (e.g. a session transcript) rather than a slug this process just
            captured.
    """
    from .resolve import central_state_dir, validate_workspace_slug

    validate_workspace_slug(slug)
    return central_state_dir(group, env=env) / "worktrees" / slug


def manifest_path_for(group: str, slug: str, *, env: dict[str, str] | None = None) -> Path:
    """Return the canonical central manifest path for (group, slug).

    Args:
        group:  Group name (validated by central_state_dir).
        slug:   Worktree slug.
        env:    Optional env override for the resolver (hermetic tests).

    Returns:
        Absolute path to manifest.json (directory may not exist yet).
    """
    return workspace_dir(group, slug, env=env) / "manifest.json"
