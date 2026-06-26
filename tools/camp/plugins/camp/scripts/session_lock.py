"""Workspace session lockfile (refuse-concurrent).

One persisted harness session per workspace. The lockfile records the live PID
that holds the session plus a session-start timestamp:

    {"pid": <int>, "started_at": "<ISO8601 UTC>", "workspace": "<path>"}

acquire_session_lock refuses if the lock is held by a LIVE PID within the age
threshold (naming workspace + PID + timestamp). A dead-PID lock is reclaimed
immediately; a live-PID lock older than the threshold is reclaimed too — the
age-bound fallback against PID recycling (psutil is absent from the venv, so we
cannot compare a process create_time; the timestamp is the only recycling guard).

The lock is held for the session's lifetime and reclaimed on the NEXT
acquire_session_lock call via dead-PID liveness detection. It is intentionally
never released during the process: camp ai ends in os.execvp into the harness,
so no Python exit path runs and no finalizer/atexit can clear it. A subsequent
camp ai call detects the dead PID (ProcessLookupError from os.kill) and reclaims
the lock before acquiring a new one. The age-bound timestamp covers the rare
edge case of PID recycling within the threshold window.

release_session_lock is defined for completeness and test use; production code
does not call it (the execvp contract means there is no point in the call chain
where it would run reliably).

Liveness: os.kill(pid, 0) → ProcessLookupError means dead;
PermissionError means alive-but-not-ours (macOS DOES raise EPERM for cross-user
PIDs) — treat as alive, conservative.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Conservative age bound for the PID-recycling fallback. A live-PID lock older
# than this is treated as stale (the original session is assumed long gone and
# the PID recycled). 24h is generous for an interactive dev session.
STALE_AFTER_SECONDS = 24 * 60 * 60

_LOCK_NAME = ".session.lock"


class SessionLockHeld(Exception):
    """Raised when a workspace session lock is held by a live PID.

    The message names the workspace path, the holding PID, and the session-start
    timestamp so the user can identify the live session.
    """


def lock_path_for(workspace_dir: Path) -> Path:
    return Path(workspace_dir) / _LOCK_NAME


def is_pid_alive(pid: int) -> bool:
    """Return True if a process with this PID exists."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:  # ESRCH — dead
        return False
    except PermissionError:  # EPERM — alive but not ours (macOS raises this)
        return True


def _is_stale(lock: dict, *, now: datetime) -> bool:
    """A lock is stale (reclaimable) if its PID is dead, or it is alive but the
    session-start timestamp is older than STALE_AFTER_SECONDS (PID-recycling
    fallback)."""
    pid = lock.get("pid")
    if not isinstance(pid, int):
        return True
    if not is_pid_alive(pid):
        return True
    started_raw = lock.get("started_at")
    if not isinstance(started_raw, str):
        return True
    try:
        started = datetime.fromisoformat(started_raw)
    except ValueError:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    age = (now - started).total_seconds()
    return age > STALE_AFTER_SECONDS


def acquire_session_lock(
    workspace_dir: Path,
    *,
    pid: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Acquire the workspace session lock, reclaiming a stale one.

    Raises SessionLockHeld if a live PID holds the lock within the age threshold.
    On success writes {pid, started_at, workspace} and returns it.
    """
    workspace_dir = Path(workspace_dir)
    now = now or datetime.now(timezone.utc)
    pid = pid if pid is not None else os.getpid()
    lock_file = lock_path_for(workspace_dir)

    if lock_file.exists():
        try:
            existing = json.loads(lock_file.read_text())
        except (ValueError, OSError):
            existing = None
        if isinstance(existing, dict) and not _is_stale(existing, now=now):
            raise SessionLockHeld(
                f"camp ai: a live session already holds {workspace_dir}\n"
                f"  PID: {existing.get('pid')}\n"
                f"  started_at: {existing.get('started_at')}\n"
                f"  refuse-concurrent: native claude resume is not multiplexable; "
                f"close that session first."
            )

    workspace_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "pid": pid,
        "started_at": now.isoformat(),
        "workspace": str(workspace_dir),
    }
    tmp = lock_file.with_suffix(lock_file.suffix + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(str(tmp), str(lock_file))
    return data


def release_session_lock(workspace_dir: Path) -> None:
    """Clear the workspace session lock. Idempotent."""
    lock_path_for(workspace_dir).unlink(missing_ok=True)
