"""Race-safe session-RECORD capture + ``session_id`` / worktree-name sanitization.

This module owns the **session** endpoint's storage primitive (Slice 1). A session
**is a first-class record** under the singular ``session/`` kind dir: a ``.md`` body
(the capture log) plus a ``.json`` sidecar carrying ``kind: session`` + a clean/dirty
status — so it is natively SQLite-indexed and KQL-discoverable
(``lore search 'kind:session status:dirty'``). This collapses the former "two
worlds" defect (body-only ``sessions/`` plural, unindexed, vs. indexed ``session/``
records produced only by migration) into one world.

This is a deliberate **rewrite** of the pre-Slice-1 contract, which wrote a body-only
file under plural ``sessions/`` and asserted it "never touches the derived SQLite
index". After Slice 1 the capture path DOES write the sidecar and DOES reindex the
one record per candidate — inside the lock (see the flock invariant below).

Three responsibilities live here:

  1. :func:`sanitize_session_id` — the **confinement guard** for the GUID key. The
     session id becomes the record filename, so it is a trust boundary for
     ``session/`` (council/Security). Rejects a path separator, a ``..`` component,
     a NUL byte, or anything not matching the canonical GUID shape Claude Code
     session ids take. Validate-or-raise; call it at the CLI entry point BEFORE any
     path is constructed.

  2. :func:`sanitize_worktree_name` — the **sibling confinement guard** for the
     worktree-name fallback key (council/Security Critical 3). The spec keys a
     session by ``--session-id`` (a GUID) **or** ``detect_worktree_name()``.
     :func:`sanitize_session_id` is GUID-only and would reject every worktree name,
     so it CANNOT guard that path. A worktree literally named ``../../evil`` must
     never become ``session/../../evil.md``. This guard admits only a bounded
     ``[A-Za-z0-9_-]+`` allowlist; everything else raises.

  3. :func:`capture_candidate` / :func:`capture_referenced` — the **race-safe
     capture primitives** (KU1, VALIDATED on darwin).

**flock invariant (KU1, proven — DO NOT relax).** The pre-Slice-1 lock covered ONLY
the body ``.md`` append; the sidecar write + reindex sat OUTSIDE it and were proven
racy (torn JSON from concurrent ``write_text``, a stale FTS snapshot where the body
holds an entry the index doesn't, and ``sqlite3.OperationalError: database is
locked``). Slice 1 therefore extends the held ``fcntl.flock`` LOCK_EX to span ONE
critical section:

    existence-check → lazy-create-or-ensure-dirty (sidecar) → body-append →
    open-index + ``upsert_row`` + ``conn.commit()``

so a concurrent second candidate can never interleave between the body append and
the reindex. Per-candidate ``upsert_row``+commit is ~0.1-0.2ms (negligible inside
the lock; KU1 timing).

  - The lock object is a SEPARATE sidecar ``<key>.lock`` file, NOT the record — so
    concurrent *reads* are never blocked and the record fds are never aliased.
  - ``open(lock_path, "a")`` create-or-opens the lock without truncating.
  - All callers for the SAME key queue on the lock; DIFFERENT keys never contend.
  - Releasing happens in ``finally`` via ``LOCK_UN`` + close.

The dominant unguarded failure mode is **LOST ENTRIES / STALE INDEX**, not
double-create. The behavioral test asserts "exactly one record" AND "all body
entries present" AND "the FTS body reflects the final body" over many concurrent
iterations.

Pure stdlib (``fcntl``, ``re``, ``json``, ``pathlib``) + the sibling ``index_store``
and ``record_store`` modules. ``fcntl`` is stdlib on darwin/linux; flock is
unreliable over NFS (non-issue — the vault is local git). darwin ``LOCK_UN == 8``.
References: Slice 1, KU1, KU2, council/Security Critical 3, council/Reliability
Critical 2.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import re
from pathlib import Path
from typing import Any, Callable

import index_store as index_store_mod
import record_store as record_store_mod

# Canonical UUID shape (Claude Code session ids are UUIDs, e.g. v4). We accept the
# standard 8-4-4-4-12 hex form, case-insensitive. This is intentionally strict: it
# admits no path separator, ``..``, NUL, or whitespace by construction, so a valid
# id is always a safe single-segment filename inside ``session/``.
_GUID_RE = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)

# Worktree-name allowlist: letters, digits, underscore, hyphen — and nothing else.
# By construction this admits no path separator, ``.`` (so no ``..``), NUL, or
# whitespace, so a valid worktree name is always a safe single-segment filename.
_WORKTREE_RE = re.compile(r"\A[A-Za-z0-9_-]+\Z")

# Bounded length for a worktree-name key (defense-in-depth against absurd names).
_WORKTREE_MAX_LEN = 128


class InvalidSessionIdError(ValueError):
    """A session key (GUID or worktree name) is not safe (Slice 1, council/Security).

    Raised by :func:`sanitize_session_id` / :func:`sanitize_worktree_name` for
    anything containing a path separator, a ``..`` component, a NUL byte, or
    otherwise off the canonical shape. The guard is the confinement boundary for the
    ``session/`` directory: a rejected key never reaches a path, so a session write
    can never escape ``session/`` (no traversal, no clobber outside the vault).
    """


def sanitize_session_id(session_id: str) -> str:
    """Validate *session_id* and return it verbatim, or raise (Slice 1, Security).

    The id becomes the record filename (``session/<session_id>.{md,json}``), so this
    is the trust boundary for the GUID key. Rejects (→ :class:`InvalidSessionIdError`):
      - a NUL byte (defense-in-depth: ``pathlib`` silently strips NUL).
      - a path separator (``/`` or ``\\``) or a ``..`` component.
      - anything not matching the canonical 8-4-4-4-12 hex GUID shape.

    A valid id is returned **unchanged** — it is the on-disk record name.
    """
    if not isinstance(session_id, str) or not session_id:
        raise InvalidSessionIdError("session_id is required")
    if "\x00" in session_id:
        raise InvalidSessionIdError("session_id must not contain a NUL byte")
    if "/" in session_id or "\\" in session_id:
        raise InvalidSessionIdError(
            f"session_id must not contain a path separator: {session_id!r}"
        )
    if ".." in session_id:
        raise InvalidSessionIdError(
            f"session_id must not contain a '..' component: {session_id!r}"
        )
    if not _GUID_RE.match(session_id):
        raise InvalidSessionIdError(
            f"session_id must be a canonical GUID (8-4-4-4-12 hex): {session_id!r}"
        )
    return session_id


def sanitize_worktree_name(name: str) -> str:
    """Validate a worktree-name session key and return it verbatim, or raise.

    The sibling confinement guard (council/Security Critical 3) for the
    ``detect_worktree_name()`` fallback key, which :func:`sanitize_session_id`
    cannot guard (it is GUID-only). The name becomes the record filename
    (``session/<name>.{md,json}``), so a worktree named ``../../evil`` must be
    rejected here BEFORE any path is built. Admits only a bounded
    ``[A-Za-z0-9_-]+`` allowlist — which excludes ``/``, ``\\``, ``.`` (hence
    ``..``), NUL, and whitespace by construction; everything else raises
    :class:`InvalidSessionIdError`.
    """
    if not isinstance(name, str) or not name:
        raise InvalidSessionIdError("worktree name is required")
    if "\x00" in name:
        raise InvalidSessionIdError("worktree name must not contain a NUL byte")
    if len(name) > _WORKTREE_MAX_LEN:
        raise InvalidSessionIdError(
            f"worktree name exceeds {_WORKTREE_MAX_LEN} chars: {name!r}"
        )
    if not _WORKTREE_RE.match(name):
        raise InvalidSessionIdError(
            f"worktree name must match [A-Za-z0-9_-]+ (no separators/'..'/dots/"
            f"spaces): {name!r}"
        )
    return name


def _now_utc_z() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _header(key: str) -> str:
    """The lazy-create body header written ONCE on first use for a key."""
    return f"# session: {key}\n\n"


def _synthetic_title(key: str) -> str:
    """A synthetic ``title`` for a session record (Slice 1).

    The key (GUID or worktree name) when present; never empty. Callers always pass a
    sanitized non-empty key, so this is simply the key — a fallback ``session
    <date>`` is here only for defensive completeness.
    """
    return key or f"session {dt.date.today().isoformat()}"


def _new_sidecar(key: str, committer: str, now: str) -> dict[str, Any]:
    """A fresh, born-``dirty`` session sidecar (Slice 1).

    Mirrors the record sidecar shape (``record_model.FIELDS_V1``) so the record is a
    valid, indexable ``session`` record: ``version``/``kind``/``title``/``status`` +
    provenance + an empty ``annotations`` map for ``last-referenced-at``/``flushed-at``.
    """
    return {
        "version": "v1",
        "kind": "session",
        "title": _synthetic_title(key),
        "status": "dirty",
        "created-at": now,
        "created-by": committer,
        "updated-at": now,
        "updated-by": committer,
        "annotations": {},
    }


def _reindex(conn, vault_root: str, key: str, sidecar: dict[str, Any], body: str) -> None:
    """Single-record reindex of the session record (KU1 — inside the lock)."""
    index_store_mod.upsert_row(conn, vault_root, "session", key, sidecar, body)
    conn.commit()


def capture_candidate(
    key: str,
    entry: str,
    *,
    vault_root: str,
    committer: str,
    open_index: Callable[[], Any],
) -> None:
    """Race-safe materialize-or-update the session record and append *entry*.

    The first candidate for *key* lazy-creates ``session/<key>.{md,json}`` born
    ``dirty``; a subsequent candidate ensures ``dirty`` (flipping a ``clean`` record
    back) and bumps ``updated-at``. In both cases *entry* (the ``- candidate …``
    block) is appended to the body and the one record is reindexed.

    The KU1 critical section: the existence-check, the sidecar create-or-ensure-dirty,
    the body append, and the ``upsert_row``+commit ALL happen inside a single held
    ``fcntl.flock`` LOCK_EX on a sidecar ``<key>.lock`` file (see the module
    docstring). *open_index* is a no-arg factory returning a fresh index connection
    (so test isolation via ``XDG_STATE_HOME`` flows through); the connection is
    opened and closed inside the lock.

    Caller MUST pass an already-sanitized *key* (see :func:`sanitize_session_id` /
    :func:`sanitize_worktree_name`) — this primitive trusts its input as a safe
    filename and does not re-validate.
    """
    session_dir = Path(vault_root) / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    body_path = session_dir / f"{key}.md"
    sidecar_path = session_dir / f"{key}.json"
    lock_path = session_dir / f"{key}.lock"

    lock_fd = open(lock_path, "a")  # create-or-open, no truncate; held until unlock
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)  # blocks until exclusive
        now = _now_utc_z()

        # Sidecar: lazy-create born-dirty, OR ensure-dirty + bump updated-at.
        if sidecar_path.exists():
            sidecar = json.loads(sidecar_path.read_text())
            sidecar["status"] = "dirty"
            sidecar["updated-at"] = now
            sidecar["updated-by"] = committer
        else:
            sidecar = _new_sidecar(key, committer, now)
        record_store_mod.write_temp_then_rename(
            sidecar_path, json.dumps(sidecar, sort_keys=True, separators=(",", ":"))
        )

        # Body: lazy-create header on first use, then append the entry.
        if not body_path.exists():
            body_path.write_text(_header(key))
        with open(body_path, "a") as bf:
            bf.write(entry + "\n")

        # Reindex this one record from the just-written disk state (inside the lock).
        body = body_path.read_text()
        conn = open_index()
        try:
            _reindex(conn, str(vault_root), key, sidecar, body)
        finally:
            conn.close()
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()


def capture_referenced(
    key: str,
    entry: str,
    *,
    vault_root: str,
    committer: str,
    open_index: Callable[[], Any],
) -> bool:
    """Race-safe append a ``- referenced`` *entry* to an EXISTING session record.

    KU2 contract: ``referenced`` on a **non-existent** session is a **no-op — it
    creates NOTHING** (returns ``False``). On an **existing** session it appends the
    body line, bumps ``last-referenced-at`` in the sidecar ``annotations`` map, and
    reindexes — but **never flips status** (never dirties, never cleans). Returns
    ``True`` when an existing record was updated.

    Same KU1 critical section as :func:`capture_candidate`: the existence-check, the
    sidecar bump, the body append, and the reindex are one held-lock unit.
    """
    session_dir = Path(vault_root) / "session"
    body_path = session_dir / f"{key}.md"
    sidecar_path = session_dir / f"{key}.json"
    lock_path = session_dir / f"{key}.lock"

    # No record (neither artifact) → no-op, create nothing (KU2). Checked outside the
    # lock as a fast path; re-checked inside the lock before any write.
    if not body_path.exists() and not sidecar_path.exists():
        return False

    session_dir.mkdir(parents=True, exist_ok=True)
    lock_fd = open(lock_path, "a")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        if not body_path.exists() and not sidecar_path.exists():
            return False
        now = _now_utc_z()

        # Body: lazy-create header on first use, then append the referenced line.
        if not body_path.exists():
            body_path.write_text(_header(key))
        with open(body_path, "a") as bf:
            bf.write(entry + "\n")

        # Bump last-referenced-at + reindex ONLY when a proper sidecar exists. A
        # body-only legacy record (no sidecar) is never indexed and carries no status;
        # fabricating a ``{}`` sidecar would write a malformed sidecar to disk and
        # project an off-vocab ``status:""`` row into the index — and would violate the
        # KU2 rule that ``referenced`` never materializes a record. Touch the body only
        # here and leave normalization of legacy shapes to the S7 migration.
        if sidecar_path.exists():
            sidecar = json.loads(sidecar_path.read_text())
            annotations = sidecar.get("annotations")
            if not isinstance(annotations, dict):
                annotations = {}
            annotations["last-referenced-at"] = now
            sidecar["annotations"] = annotations
            sidecar["updated-at"] = now
            sidecar["updated-by"] = committer
            record_store_mod.write_temp_then_rename(
                sidecar_path, json.dumps(sidecar, sort_keys=True, separators=(",", ":"))
            )
            body = body_path.read_text()
            conn = open_index()
            try:
                _reindex(conn, str(vault_root), key, sidecar, body)
            finally:
                conn.close()
        return True
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()
