"""Race-safe session-RECORD capture + ``session_id`` / worktree-name sanitization.

This module owns the **session** endpoint's storage primitive. A session
**is a first-class record** under the singular ``session/`` kind dir: a ``.md`` body
(the capture log) plus a ``.json`` sidecar carrying ``kind: session`` + a clean/dirty
status — so it is natively SQLite-indexed and KQL-discoverable
(``lore search 'kind:session status:dirty'``). This collapses the former "two
worlds" defect (body-only ``sessions/`` plural, unindexed, vs. indexed ``session/``
records produced only by migration) into one world.

This is a deliberate **rewrite** of the earlier contract, which wrote a body-only
file under plural ``sessions/`` and asserted it "never touches the derived SQLite
index". Now the capture path DOES write the sidecar and DOES reindex the
one record per candidate — inside the lock (see the flock invariant below).

Three responsibilities live here:

  1. :func:`sanitize_session_id` — the **confinement guard** for the GUID key. The
     session id becomes the record filename, so it is a trust boundary for
     ``session/``. Rejects a path separator, a ``..`` component,
     a NUL byte, or anything not matching the canonical GUID shape Claude Code
     session ids take. Validate-or-raise; call it at the CLI entry point BEFORE any
     path is constructed.

  2. :func:`sanitize_worktree_name` — the **sibling confinement guard** for the
     worktree-name fallback key. The session is keyed
     by ``--session-id`` (a GUID) **or** ``detect_worktree_name()``.
     :func:`sanitize_session_id` is GUID-only and would reject every worktree name,
     so it CANNOT guard that path. A worktree literally named ``../../evil`` must
     never become ``session/../../evil.md``. This guard admits only a bounded
     ``[A-Za-z0-9_-]+`` allowlist; everything else raises.

  3. :func:`capture_candidate` / :func:`capture_referenced` — the **race-safe
     capture primitives**.

**flock invariant (proven — DO NOT relax).** The earlier lock covered ONLY
the body ``.md`` append; the sidecar write + reindex sat OUTSIDE it and were proven
racy (torn JSON from concurrent ``write_text``, a stale FTS snapshot where the body
holds an entry the index doesn't, and ``sqlite3.OperationalError: database is
locked``). The lock therefore spans ONE critical section:

    existence-check → lazy-create-or-ensure-dirty (sidecar) → body-append →
    open-index + ``upsert_row`` + ``conn.commit()``

so a concurrent second candidate can never interleave between the body append and
the reindex. Per-candidate ``upsert_row``+commit is ~0.1-0.2ms (negligible inside
the lock).

The lock itself is :func:`lore.locking.session_write_lock` — the shared blocking
flock helper every lore writer uses (see that module for the acquisition
semantics, the lock-wait stderr notice, and the single-user threat model). Its
granularity here is **(vault, session key)**, an exclusive flock on a separate
sidecar ``session/<key>.lock`` file: all callers for the SAME key queue on it,
DIFFERENT keys never contend, and concurrent *readers* take no lock at all. It is
deliberately NOT the vault-wide ``.lore.lock`` — a session capture must not block
an unrelated ``record`` write, and vice versa.

The dominant unguarded failure mode is **LOST ENTRIES / STALE INDEX**, not
double-create. The behavioral test asserts "exactly one record" AND "all body
entries present" AND "the FTS body reflects the final body" over many concurrent
iterations.

Pure stdlib (``re``, ``json``, ``pathlib``) + the sibling ``locking``,
``index_store``, and ``record_store`` modules.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Callable

from .. import locking
from ..record import sidecar as sidecar_format
from ..record import store as record_store_mod
from ..search import index as index_store_mod

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
    """A session key (GUID or worktree name) is not safe.

    Raised by :func:`sanitize_session_id` / :func:`sanitize_worktree_name` for
    anything containing a path separator, a ``..`` component, a NUL byte, or
    otherwise off the canonical shape. The guard is the confinement boundary for the
    ``session/`` directory: a rejected key never reaches a path, so a session write
    can never escape ``session/`` (no traversal, no clobber outside the vault).
    """


def sanitize_session_id(session_id: str) -> str:
    """Validate *session_id* and return it verbatim, or raise.

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
        raise InvalidSessionIdError(f"session_id must not contain a path separator: {session_id!r}")
    if ".." in session_id:
        raise InvalidSessionIdError(f"session_id must not contain a '..' component: {session_id!r}")
    if not _GUID_RE.match(session_id):
        raise InvalidSessionIdError(
            f"session_id must be a canonical GUID (8-4-4-4-12 hex): {session_id!r}"
        )
    return session_id


def sanitize_worktree_name(name: str) -> str:
    """Validate a worktree-name session key and return it verbatim, or raise.

    The sibling confinement guard for the
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


# --- flushed-at: the flush watermark ------------------------------------------
#
# `lore flush` (the producer) stamps this; the `/lore:flush` skill (the
# consumer) reads it to tell already-flushed candidate lines from new ones. Both
# the KEY and the FORMAT are PINNED here as the single source of truth — drift
# between producer and consumer would silently drop candidates.
#
#   - Stored inside the free-form sidecar ``annotations`` map (NOT a top-level
#     key — ``record_model``'s schema is closed at the top level and would reject
#     it). ``annotations`` is sidecar-only: it is NOT projected to the index, so
#     readers load the ``.json`` sidecar directly (there is no ``annotations``
#     column / KQL field).
#   - Format ``%Y-%m-%dT%H:%M:%SZ`` (whole-second UTC, ``Z`` suffix) — identical
#     to the candidate-line timestamp, so "outstanding = candidate line
#     timestamped after flushed-at" is a plain lexicographic / ISO comparison.
FLUSHED_AT_KEY = "flushed-at"
FLUSHED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def parse_flushed_at(raw: Any) -> dt.datetime | None:
    """Validate-before-trust reader for the flush watermark.

    Parse *raw* (the value at ``annotations[FLUSHED_AT_KEY]``) and return an
    aware UTC ``datetime``, or ``None`` on ANY failure — missing, empty, non-str,
    corrupt, naive (no tzinfo), or a non-UTC offset. ``None`` means "no prior
    flush": the consumer treats EVERY candidate as outstanding (conservative — it
    never silently drops candidates on a corrupt watermark). A valid *future*
    value parses successfully (a future watermark is an application concern, not a
    parse error). Never raises.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        return None
    return parsed


def _now_utc_z() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime(FLUSHED_AT_FORMAT)


def _header(key: str) -> str:
    """The lazy-create body header written ONCE on first use for a key."""
    return f"# session: {key}\n\n"


def _synthetic_title(key: str) -> str:
    """A synthetic ``title`` for a session record.

    The key (GUID or worktree name) when present; never empty. Callers always pass a
    sanitized non-empty key, so this is simply the key — a fallback ``session
    <date>`` is here only for defensive completeness.
    """
    return key or f"session {dt.date.today().isoformat()}"


def _new_sidecar(key: str, committer: str, now: str) -> dict[str, Any]:
    """A fresh, born-``dirty`` session sidecar.

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
    """Single-record reindex of the session record (inside the lock)."""
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

    The critical section: the existence-check, the sidecar create-or-ensure-dirty,
    the body append, and the ``upsert_row``+commit ALL happen inside a single held
    :func:`lore.locking.session_write_lock` (see the module docstring). *open_index*
    is a no-arg factory returning a fresh index connection
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

    with locking.session_write_lock(vault_root, key):
        now = _now_utc_z()

        # Sidecar: lazy-create born-dirty, OR ensure-dirty + bump updated-at.
        if sidecar_path.exists():
            sidecar = json.loads(sidecar_path.read_text())
            sidecar["status"] = "dirty"
            sidecar["updated-at"] = now
            sidecar["updated-by"] = committer
        else:
            sidecar = _new_sidecar(key, committer, now)
        record_store_mod.write_temp_then_rename(sidecar_path, sidecar_format.dumps(sidecar))

        # Body: lazy-create header on first use, then append the entry.
        if not body_path.exists():
            body_path.write_text(_header(key))
        with open(body_path, "a") as bf:
            bf.write(entry + "\n")

        # Reindex this one record from the just-written disk state (inside the lock).
        body = body_path.read_text()
        conn = open_index()
        try:
            # A reserved-key label row (or any other malformed-projection
            # exception) degrades to a skipped index row for this one record
            # rather than failing the whole capture — mirrors rebuild()'s
            # existing per-record skip-on-exception guard.
            try:
                _reindex(conn, str(vault_root), key, sidecar, body)
            except Exception:
                pass
        finally:
            conn.close()


# Flush verdicts. The CLI maps these to user-facing notices + the
# commit decision: only ``"flushed"`` mutates the record and warrants a commit.
FLUSH_FLUSHED = "flushed"      # a dirty session was flipped clean + stamped
FLUSH_ALREADY_CLEAN = "clean"  # session exists but was already clean — no-op
FLUSH_NO_SESSION = "no-session"  # no record for this key — distinct from clean


def session_exists(vault_root: str, key: str) -> bool:
    """Return whether a session record exists for *key* — creating nothing.

    The lock-free precondition probe. A caller that wants the flip and its own
    follow-on work (``lore flush``'s commit) to be ONE locked unit has to take the
    session lock before calling :func:`flush_session`, and taking a lock creates
    its ``session/<key>.lock`` sidecar — turning a "no session here" flush into a
    command that dirties the vault. Probing first keeps that no-op inert.
    ``flush_session`` re-checks under the lock, so this is a fast path, not the
    guarantee.
    """
    return (Path(vault_root) / "session" / f"{key}.json").exists()


def flush_session(
    key: str,
    *,
    vault_root: str,
    committer: str,
    open_index: Callable[[], Any],
) -> str:
    """Mechanically flush ONE session record by *key*.

    Resolves ``session/<key>.json``:
      - no sidecar → returns :data:`FLUSH_NO_SESSION` (the CLI distinguishes this
        from an already-clean session); writes nothing.
      - status already ``clean`` → returns :data:`FLUSH_ALREADY_CLEAN`; idempotent
        no-op, no write, no reindex.
      - status ``dirty`` → flips it ``clean``, stamps ``annotations[FLUSHED_AT_KEY]``
        with the pinned ISO-UTC format, bumps ``updated-at``, reindexes the one
        record, and returns :data:`FLUSH_FLUSHED`.

    The flip never writes ``status: complete`` / ``active`` — those vocab values
    were retired; a session status is only ever ``dirty`` / ``clean``.

    Same critical section as :func:`capture_candidate`: the existence/status
    check, the sidecar rewrite, and the ``upsert_row``+commit are ONE held-lock
    unit, so a concurrent candidate cannot interleave between the status read and
    the reindex. Caller MUST pass an already-sanitized *key*.
    """
    session_dir = Path(vault_root) / "session"
    sidecar_path = session_dir / f"{key}.json"
    body_path = session_dir / f"{key}.md"

    # Fast path: no record at all → no-op, create nothing. Re-checked under lock.
    if not sidecar_path.exists():
        return FLUSH_NO_SESSION

    session_dir.mkdir(parents=True, exist_ok=True)
    with locking.session_write_lock(vault_root, key):
        if not sidecar_path.exists():
            return FLUSH_NO_SESSION
        sidecar = json.loads(sidecar_path.read_text())
        if sidecar.get("status") == "clean":
            return FLUSH_ALREADY_CLEAN

        now = _now_utc_z()
        sidecar["status"] = "clean"
        annotations = sidecar.get("annotations")
        if not isinstance(annotations, dict):
            annotations = {}
        annotations[FLUSHED_AT_KEY] = now
        sidecar["annotations"] = annotations
        sidecar["updated-at"] = now
        sidecar["updated-by"] = committer
        record_store_mod.write_temp_then_rename(sidecar_path, sidecar_format.dumps(sidecar))

        body = body_path.read_text() if body_path.exists() else ""
        conn = open_index()
        try:
            # See capture_candidate: a malformed-projection exception (e.g. a
            # reserved-key label row) degrades to a skipped index row rather
            # than failing the flush.
            try:
                _reindex(conn, str(vault_root), key, sidecar, body)
            except Exception:
                pass
        finally:
            conn.close()
        return FLUSH_FLUSHED


def revert_flush(
    key: str,
    *,
    vault_root: str,
    committer: str,
    open_index: Callable[[], Any],
) -> None:
    """Roll a session back ``clean`` → ``dirty`` + drop the ``flushed-at`` stamp.

    The batch-flush recovery primitive: :func:`flush_session` flips the sidecar +
    index ``clean`` BEFORE the git commit, so a commit failure would otherwise leave
    the session clean-on-disk-but-uncommitted — and the discovery query
    (``kind:session status:dirty``) would never re-find it, so a re-run could not
    retry it. Reverting the flip keeps the per-session unit atomic FROM THE USER'S
    VIEW (fully flushed+committed, or left dirty for retry) and makes a batch re-run
    rediscover the failed session.

    Idempotent + best-effort: a session that is already ``dirty`` (or has no record)
    is left untouched. Runs under the same per-key lock as :func:`flush_session`.
    """
    session_dir = Path(vault_root) / "session"
    sidecar_path = session_dir / f"{key}.json"
    body_path = session_dir / f"{key}.md"

    if not sidecar_path.exists():
        return

    session_dir.mkdir(parents=True, exist_ok=True)
    with locking.session_write_lock(vault_root, key):
        if not sidecar_path.exists():
            return
        sidecar = json.loads(sidecar_path.read_text())
        if sidecar.get("status") != "clean":
            return
        now = _now_utc_z()
        sidecar["status"] = "dirty"
        annotations = sidecar.get("annotations")
        if isinstance(annotations, dict):
            annotations.pop(FLUSHED_AT_KEY, None)
            sidecar["annotations"] = annotations
        # Re-stamp provenance to the revert: leaving updated-at/by at the rolled-back
        # flush's timestamp would show an update that never committed. The revert is
        # itself the most recent real mutation of the record.
        sidecar["updated-at"] = now
        sidecar["updated-by"] = committer
        record_store_mod.write_temp_then_rename(sidecar_path, sidecar_format.dumps(sidecar))
        body = body_path.read_text() if body_path.exists() else ""
        conn = open_index()
        try:
            # See capture_candidate: a malformed-projection exception (e.g. a
            # reserved-key label row) degrades to a skipped index row rather
            # than failing the revert.
            try:
                _reindex(conn, str(vault_root), key, sidecar, body)
            except Exception:
                pass
        finally:
            conn.close()


def capture_referenced(
    key: str,
    entry: str,
    *,
    vault_root: str,
    committer: str,
    open_index: Callable[[], Any],
) -> bool:
    """Race-safe append a ``- referenced`` *entry* to an EXISTING session record.

    Contract: ``referenced`` on a **non-existent** session is a **no-op — it
    creates NOTHING** (returns ``False``). On an **existing** session it appends the
    body line, bumps ``last-referenced-at`` in the sidecar ``annotations`` map, and
    reindexes — but **never flips status** (never dirties, never cleans). Returns
    ``True`` when an existing record was updated.

    Same critical section as :func:`capture_candidate`: the existence-check, the
    sidecar bump, the body append, and the reindex are one held-lock unit.
    """
    session_dir = Path(vault_root) / "session"
    body_path = session_dir / f"{key}.md"
    sidecar_path = session_dir / f"{key}.json"

    # No record (neither artifact) → no-op, create nothing. Checked outside the
    # lock as a fast path; re-checked inside the lock before any write.
    if not body_path.exists() and not sidecar_path.exists():
        return False

    session_dir.mkdir(parents=True, exist_ok=True)
    with locking.session_write_lock(vault_root, key):
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
        # rule that ``referenced`` never materializes a record. Touch the body only
        # here and leave normalization of legacy shapes to a later migration.
        if sidecar_path.exists():
            sidecar = json.loads(sidecar_path.read_text())
            annotations = sidecar.get("annotations")
            if not isinstance(annotations, dict):
                annotations = {}
            annotations["last-referenced-at"] = now
            sidecar["annotations"] = annotations
            sidecar["updated-at"] = now
            sidecar["updated-by"] = committer
            record_store_mod.write_temp_then_rename(sidecar_path, sidecar_format.dumps(sidecar))
            body = body_path.read_text()
            conn = open_index()
            try:
                _reindex(conn, str(vault_root), key, sidecar, body)
            finally:
                conn.close()
        return True
