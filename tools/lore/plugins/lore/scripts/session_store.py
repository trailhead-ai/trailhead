"""Race-safe session-note lazy-create + append, and ``session_id`` sanitization.

This module owns the **session** endpoint's storage primitive (Slice 6, S2). It is
a deliberately separate owner from ``record_store.py`` (council/Builder): a session
write is NOT a first-class record — the **capture** write (:func:`create_or_append`)
is body-only and produces no ``.json`` sidecar, never touches the derived SQLite
index, and never routes through ``validate_and_write`` (AC23, endpoint isolation).
**Finalize** (``sessions.finalize_note``, a separate module) DOES write a
``sessions/<GUID>.json`` metadata sidecar so session metadata lives in a sidecar —
consistent with records (``<kind>/<name>.md`` + ``<kind>/<name>.json``) rather than
in ``.md`` frontmatter; that is a finalize concern and does not change the capture
path here. Pinning the owner here keeps S7's import site unambiguous when it migrates
session logging.

Two responsibilities live here:

  1. :func:`sanitize_session_id` — the **confinement guard**. The ``session_id``
     becomes a filename, so it is the trust boundary for the ``sessions/`` directory
     (council/Security). A crafted id like ``../../evil`` would escape ``sessions/``
     and let a session write clobber files anywhere; this guard rejects anything
     containing a path separator (``/`` or ``\\``), a ``..`` component, a NUL byte,
     or otherwise not matching the canonical GUID shape that Claude Code session ids
     take (S1). It is a **validate-or-raise**: a valid id is returned verbatim (it is
     the on-disk note name, per S1's "name = the session_id GUID"); anything else
     raises :class:`InvalidSessionIdError`. Call it at the CLI entry point BEFORE any
     path is constructed.

  2. :func:`create_or_append` — the **race-safe lazy-create + append** primitive
     (AC20 / KU3, VALIDATED on darwin). Lazy-create means the session note is created
     on first use for an id and appended to thereafter; "race-safe" means two
     concurrent callers for the SAME id yield exactly ONE note with BOTH entries
     present (no lost entry, no double-create, no corruption).

**flock invariant (KU3, proven design — DO NOT relax).** The existence-check, the
lazy-create, and the append ALL happen inside a single held ``fcntl.flock`` LOCK_EX:

  - The lock object is a SEPARATE sidecar ``<session_id>.lock`` file, NOT the note
    file — so concurrent *reads* of the note are never blocked, and the note fd and
    lock fd are never aliased.
  - ``open(lock_path, "a")`` create-or-opens the lock file without truncating.
  - ``fcntl.flock(fd, LOCK_EX)`` blocks until this caller holds the exclusive lock;
    all other callers for the SAME id queue here, while DIFFERENT ids never contend.
  - Inside the lock: check existence → if absent, write the header (lazy-create) →
    append the entry. Releasing happens in ``finally`` via ``LOCK_UN`` + close.

The dominant unguarded failure mode is **LOST ENTRIES**, not double-create — the
unguarded variant flaked 67/100 (lost entries) in the KU3 proof while the guarded
variant held 0/100. The behavioral test asserts BOTH "exactly one note" AND "both
entries present" over many concurrent iterations.

Pure stdlib (``fcntl``, ``re``, ``pathlib``). References: Slice 6, S2, AC20/AC21/
AC22/AC23, AC-FENCE1, KU3. ``fcntl`` is stdlib on darwin/linux; flock is unreliable
over NFS (non-issue — the vault is local git). darwin ``LOCK_UN == 8``.
"""

from __future__ import annotations

import fcntl
import re
from pathlib import Path

# Canonical UUID shape (Claude Code session ids are UUIDs, e.g. v4). We accept the
# standard 8-4-4-4-12 hex form, case-insensitive. This is intentionally strict: it
# admits no path separator, ``..``, NUL, or whitespace by construction, so a valid
# id is always a safe single-segment filename inside ``sessions/``.
_GUID_RE = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)


class InvalidSessionIdError(ValueError):
    """The ``session_id`` is not a safe, canonical GUID (Slice 6, council/Security).

    Raised by :func:`sanitize_session_id` for anything containing a path separator,
    a ``..`` component, a NUL byte, or otherwise not matching the canonical GUID
    shape. The guard is the confinement boundary for the ``sessions/`` directory: a
    rejected id never reaches a path, so a session write can never escape
    ``sessions/`` (no traversal, no clobber outside the vault).
    """


def sanitize_session_id(session_id: str) -> str:
    """Validate *session_id* and return it verbatim, or raise (Slice 6, AC20/Security).

    The id becomes the session-note filename (``<session_id>.md``, per S1), so this
    is the trust boundary for ``sessions/``. Rejects (→ :class:`InvalidSessionIdError`):
      - a NUL byte (defense-in-depth: ``pathlib`` silently strips NUL, and execve
        already rejects it in argv, but we guard explicitly so the lib is safe for
        non-argv callers).
      - a path separator (``/`` or ``\\``) or a ``..`` component.
      - anything not matching the canonical 8-4-4-4-12 hex GUID shape.

    A valid id is returned **unchanged** — it is the on-disk note name; we never
    rewrite it (no slugging, no suffixing — KU5: ``session`` keeps the GUID verbatim).
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


def _header(session_id: str) -> str:
    """The lazy-create header written ONCE on first use for a session_id."""
    return f"# session: {session_id}\n\n"


def create_or_append(session_id: str, entry: str, sessions_dir: Path) -> None:
    """Race-safe lazy-create the session note for *session_id* and append *entry*.

    The proven KU3 design (do not relax — see the module docstring): acquire
    ``LOCK_EX`` on a sidecar ``<session_id>.lock`` file, then perform the existence
    check + lazy-create + append ALL inside the held lock, then release.

    *entry* is appended as a single line (a trailing newline is added). Callers are
    expected to have already neutralized any fence tokens and to pass a single-line
    (or pre-shaped) entry; this primitive does not parse or transform *entry*.

    Caller MUST pass an already-sanitized *session_id* (see
    :func:`sanitize_session_id`) — this primitive trusts its input as a safe
    filename and does not re-validate.
    """
    sessions_dir = Path(sessions_dir)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    note_path = sessions_dir / f"{session_id}.md"
    lock_path = sessions_dir / f"{session_id}.lock"

    lock_fd = open(lock_path, "a")  # create-or-open, no truncate; held until unlock
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)  # blocks until exclusive
        if not note_path.exists():
            note_path.write_text(_header(session_id))  # lazy-create INSIDE the lock
        with open(note_path, "a") as nf:
            nf.write(entry + "\n")  # append INSIDE the lock
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()
