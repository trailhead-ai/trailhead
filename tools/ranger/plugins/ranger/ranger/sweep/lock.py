"""The one-sweep-per-vault mutex.

Guarantees at most one sweep runs against a given vault at a time. The lock
file lives at ``state_dir("ranger")/locks/<vault_name>.lock`` and is created
with ``O_CREAT | O_EXCL`` — the OS atomically decides the race, so two
processes racing ``acquire`` can never both believe they hold the lock. Its
body is JSON: ``{"group", "pid", "host", "started_at"}``, enough for a human
to identify (and, if needed, manually kill) the holder.

Security posture — vault names become a path segment (the lock filename), so
every entry point validates confinement (no separators, no ``..``, non-empty)
before touching the filesystem at all.

Contention handling is deliberately one-directional: this module never
unlinks another process's lock and never reaps a stale one automatically.
``acquire`` on an existing lock always raises — naming the live holder when
its pid answers ``os.kill(pid, 0)`` (a ``PermissionError`` still counts as
alive: the pid exists, just owned by someone else), or reporting the file as
stale with the exact manual removal command otherwise. A lock file whose
payload can't be parsed is treated identically to a stale lock — reported for
manual removal, never deleted on the caller's behalf. Blindly unlinking a
lock out from under a still-running holder (or trusting an unreadable
payload enough to delete it) is exactly the kind of silent takeover that
turns a mutex into a race; forcing every removal through an operator's own
``rm`` keeps this module from ever creating that race itself.

``release`` is the one path that *does* remove the file, and only because it
first proves the caller is the recorded holder (pid match) — it never
removes a lock recorded under a different pid.
"""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path

from trailhead.paths import ensure_dir, state_dir

_LOCKS_SUBDIR = "locks"


class LockError(Exception):
    """Raised for any sweep-lock failure: invalid vault name, contention, or
    a release attempted against a lock the caller doesn't hold."""


def _validate_vault_name(name: str) -> None:
    if not name:
        raise LockError("vault name must not be empty")
    if "/" in name or "\\" in name or os.sep in name or ".." in name:
        raise LockError(f"vault name {name!r} must not contain path separators or '..'")


def lock_path(vault_name: str, *, env: dict[str, str] | None = None) -> Path:
    """Return the lock path for vault_name, validating confinement first."""
    _validate_vault_name(vault_name)
    return state_dir("ranger", env=env) / _LOCKS_SUBDIR / f"{vault_name}.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — alive from our perspective.
        return True
    return True


def _stale_removal_message(path: Path, *, reason: str) -> str:
    return f"{reason}; if the sweep isn't actually running, remove it manually: rm {path}"


def _raise_for_existing_lock(path: Path) -> None:
    try:
        payload = json.loads(path.read_text())
        group = payload["group"]
        pid = payload["pid"]
        host = payload["host"]
    except (OSError, ValueError, KeyError, TypeError):
        raise LockError(
            _stale_removal_message(path, reason=f"lock file {path} has an unreadable payload")
        )

    if _pid_alive(pid):
        raise LockError(
            f"a sweep is already running for group {group!r} (pid {pid} on {host!r}); "
            "refusing to start a second sweep against the same vault"
        )
    raise LockError(
        _stale_removal_message(
            path,
            reason=f"stale lock from group {group!r} (pid {pid} on {host!r} is no longer running)",
        )
    )


def acquire(vault_name: str, group: str, *, env: dict[str, str] | None = None) -> Path:
    """Create and hold the lock for vault_name, or raise LockError.

    Returns the lock path on success. Never overwrites or removes an existing
    lock file — see the module docstring for why.
    """
    path = lock_path(vault_name, env=env)
    ensure_dir(path.parent, mode=0o700)

    payload = {
        "group": group,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        _raise_for_existing_lock(path)
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f)
    return path


def release(vault_name: str, *, env: dict[str, str] | None = None) -> None:
    """Remove the lock for vault_name, but only if it records the caller's own pid.

    Raises LockError if the lock is missing, unreadable, or held by a
    different pid — release never removes a lock it can't prove is its own.
    """
    path = lock_path(vault_name, env=env)
    try:
        text = path.read_text()
    except OSError as e:
        raise LockError(f"no lock file at {path} to release: {e}")

    try:
        payload = json.loads(text)
        pid = payload["pid"]
    except (ValueError, KeyError, TypeError):
        raise LockError(f"lock file {path} has an unreadable payload; refusing to release it")

    if pid != os.getpid():
        raise LockError(
            f"lock file {path} is held by pid {pid}, not the calling process ({os.getpid()}); "
            "refusing to release a lock this process doesn't hold"
        )
    path.unlink()
