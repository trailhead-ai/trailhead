"""The global bookmark store — one ref-keyed JSON file for the whole machine.

A *bookmark* names one harness session started from one camp workspace, so it can
be listed and resumed later from anywhere. Unlike the per-workspace central
manifest, the store is GLOBAL (one file, all groups): a ref is looked up without
knowing which group it belongs to, which is the whole point of the ref.

On-disk shape (``state_dir("camp")/bookmarks.json``)::

    {"schema_version": 1, "bookmarks": {"<ref>": {…record…}}}

A record carries: ``ref``, ``group``, ``slug``, ``session_id``,
``transcript_path``, ``note``, ``created_at``, ``updated_at``.

Invariants
----------
- **Serialized.** Every read and write happens under an exclusive ``flock`` on a
  SIBLING lockfile (``bookmarks.lock``), so two concurrent captures can never
  interleave a read-modify-write and lose one of the two records. The lockfile is
  a sibling, not the store itself, so an atomic replace of the store never swaps
  the inode a holder is locking. This inherits ``flock``'s documented
  single-user caveat (a hostile local user can pre-create or symlink the lock
  path — CWE-59): the store lives under the user's own state dir, and camp
  already accepts that posture for its manifest locks.
- **Atomic.** Writes go to a temp file in the same directory and are
  ``os.replace``-d into place, then chmod'd 0o600 (umask-proof). A failure
  mid-write leaves the PRIOR store byte-intact and removes the temp file — the
  store never exists in a torn state.
- **Legible failure.** A corrupt or wrong-shaped store raises
  :class:`BookmarkStoreError` naming the file, so the CLI can print one clean
  line instead of a JSON traceback. An ABSENT store is not an error: it reads as
  "no bookmarks yet".
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

#: Bumped only when the on-disk shape changes incompatibly. Written on every
#: save so a future reader can tell which shape it is holding.
SCHEMA_VERSION = 1

#: The format every record's ``created_at``/``updated_at`` is written and read in.
#: It belongs to the on-disk shape, so writer and reader share this one
#: declaration: a divergence would not fail loudly — it would silently render
#: every age as unknown.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_STORE_FILENAME = "bookmarks.json"
_LOCK_FILENAME = "bookmarks.lock"


class BookmarkStoreError(Exception):
    """Raised when the store file is unreadable or malformed.

    The message always names the store path so a user can inspect or delete it.
    """


def _camp_state_dir(env: dict[str, str] | None = None) -> Path:
    import trailhead.paths as _paths  # lazy: the entry-point bootstrap already ran

    kwargs: dict[str, Any] = {}
    if env is not None:
        kwargs["env"] = env
    return _paths.state_dir("camp", **kwargs)


def _ensure_state_dir(env: dict[str, str] | None = None) -> Path:
    """Materialize the camp state dir owner-only and return it.

    0o700 matters as much as the store's own 0o600: a world-readable parent leaks
    every ref, group, and slug by name even when the file itself is locked down.
    """
    import trailhead.paths as _paths

    return _paths.ensure_dir(_camp_state_dir(env))


def store_path(*, env: dict[str, str] | None = None) -> Path:
    """Return the bookmark store path (the file may not exist yet)."""
    return _camp_state_dir(env) / _STORE_FILENAME


def lock_path(*, env: dict[str, str] | None = None) -> Path:
    """Return the store's sibling lockfile path (created on first write)."""
    return _camp_state_dir(env) / _LOCK_FILENAME


@contextmanager
def store_lock(*, env: dict[str, str] | None = None) -> Iterator[None]:
    """Hold the exclusive store lock for the duration of the block.

    Wrap the WHOLE read-modify-write — a lock released between the read and the
    write would let a concurrent capture's record be overwritten by this one's
    stale snapshot.
    """
    _ensure_state_dir(env)
    fd = open(str(lock_path(env=env)), "a")  # create-or-open, never truncate
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fd.close()


def _read_unlocked(path: Path) -> dict[str, dict[str, Any]]:
    """Return the ref → record mapping. An absent store reads as empty."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise BookmarkStoreError(f"camp: cannot read the bookmark store at {path}: {e}") from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise BookmarkStoreError(
            f"camp: the bookmark store at {path} is corrupt ({e}); "
            "inspect or delete the file to continue"
        ) from e

    if not isinstance(data, dict) or not isinstance(data.get("bookmarks", {}), dict):
        raise BookmarkStoreError(
            f"camp: the bookmark store at {path} is not in the expected shape; "
            "inspect or delete the file to continue"
        )
    return data.get("bookmarks", {})


def _write_unlocked(path: Path, bookmarks: dict[str, dict[str, Any]]) -> None:
    """Atomically replace the store with *bookmarks*, mode 0o600."""
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".bookmarks-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"schema_version": SCHEMA_VERSION, "bookmarks": bookmarks}, f, indent=2)
        os.replace(tmp_path, str(path))
        os.chmod(str(path), 0o600)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


@contextmanager
def transaction(*, env: dict[str, str] | None = None) -> Iterator[dict[str, dict[str, Any]]]:
    """Read-modify-write the whole store as ONE critical section.

    Yields the mutable ref → record mapping; on clean exit the mapping is written
    back atomically, and only if it actually changed (so a read-only or no-op
    transaction never creates or rewrites the file). An exception propagates with
    nothing written — a rejected capture leaves the prior store byte-intact.

    Multi-step commands (validate against what is stored, then write) MUST use
    this rather than a query followed by :func:`upsert`: the lock is not
    reentrant, and the gap between two calls is exactly where a concurrent
    capture would be lost.
    """
    path = store_path(env=env)
    with store_lock(env=env):
        bookmarks = _read_unlocked(path)
        before = json.dumps(bookmarks, sort_keys=True)
        yield bookmarks
        if json.dumps(bookmarks, sort_keys=True) != before:
            _write_unlocked(path, bookmarks)


# ---------------------------------------------------------------------------
# Read / query
# ---------------------------------------------------------------------------


def get_by_ref(ref: str, *, env: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Return the bookmark named *ref*, or None when no such bookmark exists."""
    with store_lock(env=env):
        return _read_unlocked(store_path(env=env)).get(ref)


def list_bookmarks(*, env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Return every bookmark, ordered by ref.

    Ref order (not insertion order) is the pinned contract: listing output must be
    stable across machines and across re-captures.
    """
    with store_lock(env=env):
        bookmarks = _read_unlocked(store_path(env=env))
    return [bookmarks[ref] for ref in sorted(bookmarks)]


def list_bookmarks_by_recency(*, env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Return every bookmark, most-recently-updated first.

    Ties (identical ``updated_at``) break on ref for a deterministic order —
    :func:`list_bookmarks`'s ref-ordered contract is untouched; this is a second,
    display-oriented ordering for ``camp bookmark ls``.
    """
    with store_lock(env=env):
        bookmarks = _read_unlocked(store_path(env=env))
    return sorted(
        bookmarks.values(),
        key=lambda record: (record.get("updated_at", ""), record.get("ref", "")),
        reverse=True,
    )


def find_by_workspace(
    group: str, slug: str, *, env: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """Return the bookmark pointing at workspace (*group*, *slug*), or None.

    One workspace holds at most one bookmark: re-capturing from the same workspace
    updates that record rather than accumulating a second one.
    """
    with store_lock(env=env):
        bookmarks = _read_unlocked(store_path(env=env))
    ref = ref_for_workspace(bookmarks, group, slug)
    return bookmarks[ref] if ref is not None else None


def ref_for_workspace(
    bookmarks: dict[str, dict[str, Any]], group: str, slug: str
) -> str | None:
    """Return the ref pointing at workspace (*group*, *slug*) within *bookmarks*.

    Takes an already-loaded mapping so a caller INSIDE a :func:`transaction` can
    ask the same question without re-acquiring the non-reentrant lock.
    """
    for ref in sorted(bookmarks):
        record = bookmarks[ref]
        if record.get("group") == group and record.get("slug") == slug:
            return ref
    return None


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------


def upsert(record: dict[str, Any], *, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Insert or replace *record* (keyed on its ``ref``) and return it."""
    with transaction(env=env) as bookmarks:
        bookmarks[record["ref"]] = dict(record)
    return record


def delete_by_ref(ref: str, *, env: dict[str, str] | None = None) -> bool:
    """Delete the bookmark named *ref*; return whether anything was removed."""
    removed = False
    with transaction(env=env) as bookmarks:
        removed = bookmarks.pop(ref, None) is not None
    return removed
