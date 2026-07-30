"""Vault-scoped write locking — the ONE flock idiom every lore writer shares.

Two lock scopes, one primitive:

  - :func:`vault_write_lock` — ``<vault_root>/.lore.lock``, held across a record
    mutation's whole critical section (place -> body/sidecar write -> index
    upsert). This is what serializes ``record create`` / ``update`` / ``delete``
    within a vault, closing the check-then-act stem collision in
    ``record.store.place_record`` and ``move_record``'s copy/repoint/delete
    non-atomicity.
  - :func:`session_write_lock` — ``<vault_root>/session/<key>.lock``, the
    (vault, session-key) granularity the session-store primitives already relied
    on (``capture_candidate`` / ``flush_session`` / ``revert_flush`` /
    ``capture_referenced``, formerly four copy-pasted flock blocks). Session
    writes are keyed, not vault-wide: two different sessions never contend.
  - :func:`vault_write_locks` — several vault locks at once, acquired in **sorted
    path order**. A cross-vault ``move_record`` touches two vaults, and two
    opposed moves between the same pair would deadlock under
    source-then-destination acquisition; a total order on the lock paths makes
    that impossible.

Semantics (matching the session-store idiom this replaces):

  - ``open(path, "a")`` create-or-opens the lock file without truncating; the fd
    is held for the duration and released with ``LOCK_UN`` + ``close`` in a
    ``finally``.
  - The lock is a SEPARATE sidecar file, never a record artifact, so record fds
    are never aliased and concurrent **readers** take no lock at all.
  - Acquisition **blocks**; it never fails or times out. A contended writer is
    delayed, never errored.
  - A wait past *notice_after* seconds prints a one-line stderr notice naming the
    lock's SCOPE (``vault`` vs ``session``), so a writer blocked behind another
    (e.g. a mid-drain ``lore reindex``) is distinguishable from a stuck one — and
    a wait on one session's key never reads as the whole vault being contended.
    Uncontended acquisition is silent.
  - **Reentrant per thread.** Nested acquisition of the same lock path in one
    thread is a depth bump, not a second ``flock`` — a second ``flock`` on a
    fresh fd would block on the fd this thread already holds and self-deadlock.
    The depth map is thread-local, so two threads in one process still exclude
    each other through the kernel exactly as two processes do.

**Threat model: single-user, local vault.** The lock file is opened by path with
no symlink guard (CWE-59), so a local attacker who can pre-create
``<vault_root>/.lore.lock`` as a symlink can redirect the (empty) lock file
elsewhere. This is the same posture as the session locks this generalizes: the
vault is a single-user, local, git-backed directory that the user already owns
and writes to directly, and the lock file carries no content. Do not adopt this
helper for a multi-tenant or shared-writable directory without adding an
``O_NOFOLLOW`` open.

``fcntl`` is stdlib on darwin/linux; flock is unreliable over NFS (a non-issue —
the vault is local). darwin ``LOCK_UN == 8``.
"""

from __future__ import annotations

import fcntl
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator

#: The vault-root lock file. ``*.lock`` is gitignore-scaffolded into every vault
#: (``config.installer``), so this is never staged by ``sync``'s ``git add -A``.
VAULT_LOCK_NAME = ".lore.lock"

#: Wait longer than this (seconds) and the wait is reported on stderr.
LOCK_WAIT_NOTICE_SECONDS = 2.0

#: How often to re-try a non-blocking acquisition while waiting for the notice
#: threshold. Small enough to be invisible, large enough not to spin hot.
_POLL_SECONDS = 0.01

# Per-thread reentrancy depth, keyed by resolved lock path. See the module
# docstring: thread-local, NOT process-global, so sibling threads still contend.
_local = threading.local()


def _depths() -> dict[str, int]:
    depths = getattr(_local, "depths", None)
    if depths is None:
        depths = {}
        _local.depths = depths
    return depths


def _resolve_key(lock_path: Path) -> Path:
    """Normalize a lock path for use as a reentrancy key.

    ``strict=False`` because the lock file (and, for session locks, its parent)
    may not exist yet — the point is to collapse ``..`` segments and symlinks so
    one lock file has exactly one key.
    """
    return lock_path.resolve()


def _acquire(lock_fd, scope: str, label: str, notice_after: float) -> None:
    """Blocking ``LOCK_EX``, reporting a wait that runs past *notice_after*.

    Fast path is a single non-blocking attempt. On contention we poll until the
    threshold, print the notice once, then fall back to a plain blocking
    ``flock`` — so the contract stays "blocks until acquired", never "fails".
    """
    fileno = lock_fd.fileno()
    try:
        fcntl.flock(fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    except BlockingIOError:
        pass

    deadline = time.monotonic() + notice_after
    while time.monotonic() < deadline:
        try:
            fcntl.flock(fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            time.sleep(_POLL_SECONDS)

    # The scope is part of the message on purpose: a vault-wide wait blocks every
    # writer in the vault, a session-key wait blocks only that session's writers.
    # An operator triaging "everything is hung" needs to tell the two apart.
    print(
        f"lore: waiting for the {scope} write lock ({label}) — "
        "another lore write is in progress",
        file=sys.stderr,
    )
    fcntl.flock(fileno, fcntl.LOCK_EX)


@contextmanager
def _flock(
    lock_path: Path,
    scope: str,
    label: str,
    notice_after: float = LOCK_WAIT_NOTICE_SECONDS,
) -> Iterator[None]:
    """Hold an exclusive, reentrant-per-thread flock on *lock_path*.

    The reentrancy key is the RESOLVED lock path: two spellings of one vault
    (``v`` and ``v/../v``, or a symlinked root) must share one depth entry, or a
    nested acquisition written differently misses the bump and self-deadlocks on
    the flock this thread already holds.
    """
    key = str(_resolve_key(lock_path))
    depths = _depths()
    if depths.get(key):
        depths[key] += 1
        try:
            yield
        finally:
            depths[key] -= 1
        return

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(lock_path, "a")  # create-or-open, no truncate
    depths[key] = 1
    try:
        _acquire(lock_fd, scope, label, notice_after)
        yield
    finally:
        depths[key] -= 1
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        finally:
            # Always close, even if LOCK_UN itself raised — an fd left open
            # on a failed unlock still leaks the OS-level flock, but closing
            # it here at least frees the file descriptor and keeps this
            # process's own reentrancy bookkeeping (the `depths` decrement
            # above) in sync with what's actually held.
            lock_fd.close()


def vault_write_lock(
    vault_root: str | Path,
    *,
    notice_after: float = LOCK_WAIT_NOTICE_SECONDS,
):
    """Serialize writes to one vault on ``<vault_root>/.lore.lock``.

    Wrap the whole critical section — naming/placement, the body+sidecar write,
    and the index upsert — so a concurrent writer can never interleave between
    the collision check and the write, nor between the write and the reindex.
    """
    root = Path(vault_root)
    return _flock(root / VAULT_LOCK_NAME, "vault", str(root), notice_after)


@contextmanager
def vault_write_locks(
    *vault_roots: str | Path,
    notice_after: float = LOCK_WAIT_NOTICE_SECONDS,
) -> Iterator[None]:
    """Hold several vault write locks at once, in **sorted path order**.

    The total order is what keeps two opposed cross-vault moves (A->B and B->A)
    from deadlocking. Duplicate roots collapse to one acquisition.
    """
    roots = sorted({str(Path(r)) for r in vault_roots})
    with ExitStack() as stack:
        for root in roots:
            stack.enter_context(vault_write_lock(root, notice_after=notice_after))
        yield


def session_write_lock(
    vault_root: str | Path,
    key: str,
    *,
    notice_after: float = LOCK_WAIT_NOTICE_SECONDS,
):
    """Serialize session-record writes for one (vault, session-key) pair.

    *key* MUST already be sanitized (``session.store.sanitize_session_id`` /
    ``sanitize_worktree_name``) — it becomes a filename verbatim.
    """
    session_dir = Path(vault_root) / "session"
    return _flock(session_dir / f"{key}.lock", "session", f"session/{key}", notice_after)
