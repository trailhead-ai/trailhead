"""Codex live-session lister — the module `CodexHarness.session_enumerate` runs.

Codex ships no non-interactive session lister, so `trailhead/harness/codex.py`
points `session_enumerate` at this module's file path (`sys.executable
<path-to-this-file>`) rather than `-m`: the subprocess it spawns has no
guarantee `trailhead` is on `sys.path`, since neither camp's teardown guard
nor `enumerate_records` runs it with a matching cwd or an installed package.
Run as `python <path-to-this-file> [--workspace DIR]`, the bootstrap below
puts this file's own package root on `sys.path` before importing `trailhead`,
so it resolves the Codex home from ITS OWN process environment (through the
same `codex_home` resolver the harness uses) and prints one JSON array of
`{"sessionId", "cwd", "kind", "startedAt"}` objects — one per thread whose
`<home>/thread-writer-locks/<thread-id>.lock` is currently held — to stdout,
filtered to `cwd` under `--workspace` when given.

Liveness fails closed
----------------------
A thread's lock file is the only liveness signal this seam has: on a clean
exit Codex REMOVES the lock file, but a crashed Codex leaves it present and
unlocked, so presence never means live — only an OS-level `fcntl.flock`
probe does (see `_flock_probe`/`_is_locked`). Any lock this probe cannot
DECIDE — no `fcntl` module, a permission or I/O error opening or locking the
file — is reported LIVE rather than not-live: under-reporting a live session
would let camp's teardown guard destroy a running session's workspace, while
over-reporting only costs a stale lock blocking teardown until a Codex run
sweeps it. A held lock whose rollout cannot be resolved or read, or a lock
directory or Codex home this process cannot list, makes this process exit
nonzero naming what could not be read, which the guard already treats as
"enumeration unavailable" for this harness.

Liveness relies on Codex itself taking a lock that `fcntl.flock` observes.
That has been verified against real Codex on macOS. `flock` and `fcntl`
locks are two distinct, non-interoperable locking mechanisms on Linux — a
lock Codex takes with one is invisible to a probe using the other — so this
module's fail-closed liveness contract is unverified on Linux.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

if __package__ in (None, ""):
    # Invoked by file path (`sys.executable <this-file>`), not `-m`: Python
    # puts only this file's own directory on `sys.path`, so `trailhead` is
    # not importable yet. `session_enumerate` (`trailhead/harness/codex.py`)
    # spawns this exact invocation from a caller's cwd it does not control
    # and with no guarantee `trailhead` is an installed package — insert the
    # package root this file lives under (three parents up:
    # `trailhead/harness/codex_sessions.py` -> `trailhead/harness` ->
    # `trailhead` -> repo root) before the `trailhead` import below.
    _PACKAGE_ROOT = Path(__file__).resolve().parents[2]
    if str(_PACKAGE_ROOT) not in sys.path:
        sys.path.insert(0, str(_PACKAGE_ROOT))

from trailhead.harness.codex import (
    _SESSIONS_SUBDIR,
    _is_session_id,
    _read_session_meta_payload,
    _resolve_rollout_path,
    codex_home,
)

try:
    import fcntl
except ImportError:  # pragma: no cover — exercised only on a non-POSIX platform
    fcntl = None  # type: ignore[assignment]

#: Directory under a Codex home holding one lock file per currently-writing
#: thread, plus a global `.coordination.lock` this lister ignores.
_THREAD_LOCKS_SUBDIR = "thread-writer-locks"

#: The global lock Codex's own writer-lock coordination uses — not a session,
#: never enumerated.
_COORDINATION_LOCK_NAME = ".coordination.lock"

#: The `kind` this lister always stamps — Codex has exactly one session kind
#: through this seam, so nothing else could vary it.
_KIND = "codex"


class MissingRolloutError(Exception):
    """A held lock names a thread whose rollout could not be found, or was
    found but could not be read for a usable ``cwd``.

    Raised by `enumerate_live_sessions` for the first held lock this happens
    to; `main` catches it to print the thread id to stderr and exit nonzero —
    the signal camp's teardown guard already treats as enumeration
    unavailable for this harness. Raised BEFORE the ``--workspace`` filter
    has a chance to run: an unusable rollout is not "not under the
    workspace", it is "cannot be decided at all".
    """

    def __init__(self, thread_id: str) -> None:
        super().__init__(thread_id)
        self.thread_id = thread_id


class InvalidLockFilenameError(Exception):
    """A held lock's filename does not name a usable Codex thread id.

    Raised by `enumerate_live_sessions` for a held ``*.lock`` file (other
    than the ignored ``.coordination.lock``) whose stem fails
    `_is_session_id`; `main` catches it to print the filename to stderr and
    exit nonzero rather than silently skipping a lock this process cannot
    make sense of.
    """

    def __init__(self, lock_path: Path) -> None:
        super().__init__(lock_path.name)
        self.lock_path = lock_path


class DirectoryUnavailableError(Exception):
    """The Codex home or its lock directory exists but could not be listed.

    Raised by `_require_dir_or_absent`/`_iter_thread_lock_paths` for anything
    other than the directory being simply absent (`FileNotFoundError`) —
    permission denial, a not-a-directory collision, or another `OSError`
    listing it. `main` catches it to print the path to stderr and exit
    nonzero: an unlistable directory is a fact this process cannot decide
    "no sessions" from, only a missing one is.
    """

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


def _flock_probe(path: Path) -> bool:
    """Return whether *path*'s OS lock is currently held.

    Opens a FRESH, read-only descriptor — `O_NOFOLLOW` so a lock-directory
    entry that is a symlink is never followed to whatever it points at
    (raises `OSError`/`ELOOP` instead, which `_is_locked` reports live), and
    `O_NONBLOCK` so a lock path swapped for a FIFO returns immediately
    instead of blocking on a writer that will never arrive. After opening,
    `fstat`s the descriptor and requires a regular file: anything else
    (a FIFO slipped past the `O_NONBLOCK` open, a device, a directory) is as
    undecidable as a failed open. Attempts a non-blocking exclusive `flock`:
    `BlockingIOError` means another process holds it (live); a clean acquire
    means it is not held, and this immediately releases what it just took.
    Raises (does not catch) on any other failure — no `fcntl` module, a
    permission or I/O error opening or locking the file, a non-regular
    target — leaving the fail-closed decision to `_is_locked`, the only
    caller.
    """
    if fcntl is None:
        raise OSError("fcntl module unavailable on this platform")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path}: not a regular file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _is_locked(path: Path) -> bool:
    """Fail-closed wrapper around `_flock_probe`: any raise reports LIVE.

    This is the one seam a test monkeypatches to simulate an undecidable
    probe (no `fcntl`, permission or I/O error) without needing a second
    process actually holding the lock.
    """
    try:
        return _flock_probe(path)
    except Exception:
        return True


def _require_dir_or_absent(path: Path) -> bool:
    """Return ``True`` if *path* is a directory, ``False`` if it does not exist.

    Raises `DirectoryUnavailableError` for anything else this process cannot
    call "absent" — a permission error stat'ing it, a not-a-directory
    collision, or another `OSError`. Uses `os.stat`, never `Path.is_dir()`,
    which on some platforms swallows a permission error into a bare
    ``False`` — indistinguishable from "does not exist" — rather than
    surfacing it.
    """
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise DirectoryUnavailableError(path, str(exc)) from exc
    if not stat.S_ISDIR(st.st_mode):
        raise DirectoryUnavailableError(path, "not a directory")
    return True


def _iter_thread_lock_paths(lock_dir: Path) -> list[Path]:
    """`*.lock` files directly under *lock_dir*, excluding the coordination lock.

    Lists via `os.scandir` rather than `glob`, which on an unlistable
    directory (permission denied, or traverse-only e.g. mode ``0o300``)
    silently yields no entries instead of raising — indistinguishable from a
    genuinely empty directory. Any `OSError` scanning *lock_dir* itself
    raises `DirectoryUnavailableError`; the caller has already confirmed
    *lock_dir* exists via `_require_dir_or_absent`.

    A symlink entry is included even when it is dangling (its target does
    not exist, so `entry.is_file()`'s symlink-following stat would otherwise
    drop it) — `_flock_probe`'s `O_NOFOLLOW` open then reports it live
    without ever resolving what it points at.
    """
    try:
        with os.scandir(lock_dir) as it:
            entries = list(it)
    except OSError as exc:
        raise DirectoryUnavailableError(lock_dir, str(exc)) from exc
    return sorted(
        Path(entry.path)
        for entry in entries
        if entry.name.endswith(".lock")
        and entry.name != _COORDINATION_LOCK_NAME
        and (entry.is_symlink() or entry.is_file())
    )


def _under_workspace(cwd: Path, workspace: Path) -> bool:
    """Path-segment "rooted under" test — never a bare string prefix.

    Raises (does not catch) `OSError` or `ValueError` resolving either path
    — the latter covers a `cwd` containing a NUL byte, which `Path.resolve`
    rejects before ever touching the OS. A comparison this process cannot
    decide must not silently read as "not under the workspace" — the caller
    treats the raise as an unusable-cwd case, the same fail-closed outcome
    as a missing or unreadable rollout.
    """
    return cwd.resolve().is_relative_to(workspace.resolve())


def enumerate_live_sessions(env: dict[str, str], workspace: Path | None = None) -> list[dict]:
    """Return one dict per currently-live Codex thread, as this module prints it.

    Raises `MissingRolloutError` for the first held lock whose thread id
    resolves to no rollout under `<home>/sessions/`, or whose rollout resolves
    but yields no usable ``cwd`` (unreadable, no ``session_meta`` first line,
    ``.zst``-only, or a non-string ``cwd``) — checked BEFORE the ``--workspace``
    filter, since an undecidable cwd cannot honestly be scoped either way.
    Raises `InvalidLockFilenameError` for a held lock whose filename is not a
    usable thread id. Raises `DirectoryUnavailableError` for a Codex home or
    lock directory this process cannot list (a merely absent one is "no
    sessions", not an error).
    """
    home = codex_home(env)
    if not _require_dir_or_absent(home):
        return []
    lock_dir = home / _THREAD_LOCKS_SUBDIR
    if not _require_dir_or_absent(lock_dir):
        return []
    sessions_dir = home / _SESSIONS_SUBDIR

    records: list[dict] = []
    for lock_path in _iter_thread_lock_paths(lock_dir):
        if not _is_locked(lock_path):
            continue

        thread_id = lock_path.name[: -len(".lock")]
        if not _is_session_id(thread_id):
            raise InvalidLockFilenameError(lock_path)

        rollout = _resolve_rollout_path(thread_id, sessions_dir)
        # `.zst`-compressed rollouts are never decompressed by this seam (see
        # `_extract_rollout_cwd` in `codex.py`) — a thread backed by nothing
        # but one has no way to recover a usable cwd, the same failure as no
        # rollout at all.
        if rollout is None or rollout.name.endswith(".zst"):
            raise MissingRolloutError(thread_id)

        payload = _read_session_meta_payload(rollout)
        cwd = payload.get("cwd") if payload else None
        if not isinstance(cwd, str) or not cwd:
            raise MissingRolloutError(thread_id)
        started_at = payload.get("timestamp") if payload else None

        if workspace is not None:
            try:
                under = _under_workspace(Path(cwd), workspace)
            except (OSError, ValueError):
                raise MissingRolloutError(thread_id) from None
            if not under:
                continue

        records.append(
            {"sessionId": thread_id, "cwd": cwd, "kind": _KIND, "startedAt": started_at}
        )

    return records


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="trailhead.harness.codex_sessions")
    parser.add_argument("--workspace", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ)
    try:
        records = enumerate_live_sessions(env, args.workspace)
    except MissingRolloutError as exc:
        print(
            "trailhead.harness.codex_sessions: held lock for thread "
            f"{exc.thread_id} has no rollout this process can read a cwd from",
            file=sys.stderr,
        )
        return 1
    except InvalidLockFilenameError as exc:
        print(
            "trailhead.harness.codex_sessions: held lock file "
            f"{exc.lock_path.name!r} does not name a usable Codex thread id",
            file=sys.stderr,
        )
        return 1
    except DirectoryUnavailableError as exc:
        print(
            f"trailhead.harness.codex_sessions: cannot read {exc.path}: {exc.reason}",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
