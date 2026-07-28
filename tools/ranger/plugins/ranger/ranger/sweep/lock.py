"""The one-sweep-per-vault mutex.

Guarantees at most one sweep runs against a given vault at a time. The lock
file lives at ``state_dir("ranger")/locks/<vault_name>.lock`` and is created
with ``O_CREAT | O_EXCL`` — the OS atomically decides the race, so two
processes racing ``acquire`` can never both believe they hold the lock. Its
body is JSON: ``{"group", "pid", "host", "started_at", "token"}`` — the first
four identify the holder to a human (and, if needed, let them kill it); the
fifth is how a later process proves it owns the lock.

**``pid`` is the sweep's holder, not the caller.** ``acquire`` records the pid
its caller supplies, never ``os.getpid()``. A sweep is not one process: it is
a coordinator driving a series of short-lived CLI invocations. Recording the
acquiring process's own pid would make the lock read as *stale* for the entire
lifetime of a live sweep — the acquiring process exits within milliseconds —
which invites an operator or scheduler to remove a running sweep's lock and
start a second one against the same vault. That is precisely the concurrency
this module exists to prevent, so the holder pid must name the long-lived
process that constitutes the sweep.

**``token`` is how ownership survives process boundaries.** ``acquire`` mints
a random token, writes it into the payload, and returns it; ``release``
removes the lock only for a caller that presents the matching token. Pid
equality cannot serve here (the releasing process is never the acquiring one)
and the vault name alone is not proof of anything — an out-of-order or
mistyped ``finish`` would otherwise tear down a different sweep's lock. The
token is unguessable and travels only through the sweep that minted it, so a
release is authorized by the run that took the lock or not at all.

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

``release`` is the one path that *does* remove the file, and it is owner-side
rather than contender-side: it removes only a lock whose recorded token the
caller can present. It is not reachable from ``acquire``.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
from datetime import datetime, timezone
from pathlib import Path

from trailhead.paths import ensure_dir, state_dir

_LOCKS_SUBDIR = "locks"
_TOKEN_BYTES = 16


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


def acquire(
    vault_name: str, group: str, *, holder_pid: int, env: dict[str, str] | None = None
) -> tuple[Path, str]:
    """Create and hold the lock for vault_name, or raise LockError.

    Args:
        holder_pid: The pid of the long-lived process that constitutes the
            sweep — the coordinator, not whatever short-lived process happens
            to be calling this. Liveness of *this* pid is what later callers
            test to tell a running sweep from an abandoned one, so a
            wrong value here is what turns the mutex into a race (see the
            module docstring).

    Returns ``(lock_path, token)``; the token is the caller's only means of
    releasing the lock later. Never overwrites or removes an existing lock
    file.
    """
    if not isinstance(holder_pid, int) or holder_pid <= 0:
        raise LockError(f"holder_pid must be a positive process id, got {holder_pid!r}")

    path = lock_path(vault_name, env=env)
    ensure_dir(path.parent, mode=0o700)

    token = secrets.token_hex(_TOKEN_BYTES)
    payload = {
        "group": group,
        "pid": holder_pid,
        "host": socket.gethostname(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "token": token,
    }
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        _raise_for_existing_lock(path)
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f)
    return path, token


def release(vault_name: str, *, token: str, env: dict[str, str] | None = None) -> None:
    """Remove the lock for vault_name, but only for the run that acquired it.

    The caller proves ownership by presenting the token ``acquire`` returned.
    Raises LockError if the lock is missing, unreadable, or records a
    different token — release never removes a lock it can't prove belongs to
    the caller's own run, so an out-of-order or mistyped release cannot tear
    down a sweep that is still running.
    """
    path = lock_path(vault_name, env=env)
    try:
        text = path.read_text()
    except OSError as e:
        raise LockError(f"no lock file at {path} to release: {e}")

    try:
        payload = json.loads(text)
        recorded = payload["token"]
    except (ValueError, KeyError, TypeError):
        raise LockError(f"lock file {path} has an unreadable payload; refusing to release it")

    if not secrets.compare_digest(str(recorded), str(token)):
        raise LockError(
            f"lock file {path} was acquired by a different sweep run; "
            "refusing to release a lock this run doesn't hold"
        )
    path.unlink()
