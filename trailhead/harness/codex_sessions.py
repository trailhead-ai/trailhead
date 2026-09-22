"""Codex live-session lister — the module `CodexHarness.session_enumerate` runs.

Codex ships no non-interactive session lister, so `trailhead/harness/codex.py`
points `session_enumerate` at this module (`sys.executable -m
trailhead.harness.codex_sessions`). Run as `python -m
trailhead.harness.codex_sessions [--workspace DIR]`, it resolves the Codex home
from ITS OWN process environment (through the same `codex_home` resolver the
harness uses) and prints one JSON array of `{"sessionId", "cwd", "kind",
"startedAt"}` objects — one per thread whose `<home>/thread-writer-locks/
<thread-id>.lock` is currently held — to stdout, filtered to `cwd` under
`--workspace` when given.

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
sweeps it. A held lock whose rollout cannot be resolved makes this process
exit nonzero, naming the thread id on stderr, which the guard already treats
as "enumeration unavailable" for this harness.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

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
    """A held lock names a thread whose rollout could not be resolved.

    Raised by `enumerate_live_sessions`; `main` catches it to print the
    thread id to stderr and exit nonzero — the signal camp's teardown guard
    already treats as enumeration unavailable for this harness.
    """

    def __init__(self, thread_id: str) -> None:
        super().__init__(thread_id)
        self.thread_id = thread_id


def _flock_probe(path: Path) -> bool:
    """Return whether *path*'s OS lock is currently held.

    Opens a FRESH, read-only descriptor and attempts a non-blocking exclusive
    `flock`: `BlockingIOError` means another process holds it (live); a clean
    acquire means it is not held, and this immediately releases what it just
    took. Raises (does not catch) on any other failure — no `fcntl` module,
    a permission or I/O error opening or locking the file — leaving the
    fail-closed decision to `_is_locked`, the only caller.
    """
    if fcntl is None:
        raise OSError("fcntl module unavailable on this platform")
    fd = os.open(path, os.O_RDONLY)
    try:
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


def _iter_thread_lock_paths(lock_dir: Path) -> list[Path]:
    """`*.lock` files directly under *lock_dir*, excluding the coordination lock."""
    return sorted(
        p
        for p in lock_dir.glob("*.lock")
        if p.is_file() and p.name != _COORDINATION_LOCK_NAME
    )


def _under_workspace(cwd: Path, workspace: Path) -> bool:
    """Path-segment "rooted under" test — never a bare string prefix."""
    try:
        return cwd.resolve().is_relative_to(workspace.resolve())
    except OSError:
        return False


def enumerate_live_sessions(env: dict[str, str], workspace: Path | None = None) -> list[dict]:
    """Return one dict per currently-live Codex thread, as this module prints it.

    Raises `MissingRolloutError` for the first held lock whose thread id
    resolves to no rollout under `<home>/sessions/`.
    """
    home = codex_home(env)
    lock_dir = home / _THREAD_LOCKS_SUBDIR
    if not lock_dir.is_dir():
        return []
    sessions_dir = home / _SESSIONS_SUBDIR

    records: list[dict] = []
    for lock_path in _iter_thread_lock_paths(lock_dir):
        thread_id = lock_path.name[: -len(".lock")]
        if not _is_session_id(thread_id):
            continue
        if not _is_locked(lock_path):
            continue

        rollout = _resolve_rollout_path(thread_id, sessions_dir)
        if rollout is None:
            raise MissingRolloutError(thread_id)

        payload = _read_session_meta_payload(rollout)
        cwd = payload.get("cwd") if payload else None
        started_at = payload.get("timestamp") if payload else None

        if workspace is not None:
            if not isinstance(cwd, str) or not cwd or not _under_workspace(Path(cwd), workspace):
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
            f"{exc.thread_id} has no readable rollout",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
