"""The update run lock and `trailhead update --status`.

``trailhead update`` (apply) holds an exclusive ``fcntl.flock`` on
``state_dir("trailhead")/update-run.lock`` for the life of the apply, so the
kernel — not a pidfile — decides whether an update is running: a killed holder
drops the lock with its process. The lock file is never unlinked and carries
the holder's ``{run_id, pid, started_at}``; it is opened without truncation
and written only once the lock is taken, so a loser never clobbers it. The
descriptor is non-inheritable, so a process the apply starts never keeps the
lock alive after the apply exits.

Three readers sit on the file: :func:`current_holder` (who holds it right now,
by a shared non-blocking lock attempt), :func:`read_lock_record` (what the
file last recorded, held or not) and :func:`build_status` (``--status --json``).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from trailhead import outpost_lifecycle
from trailhead.paths import ensure_dir, state_dir

RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
STATUS_SCHEMA_VERSION = 1

_LOCK_FILENAME = "update-run.lock"
_RECORD_WAIT_SECONDS = 1.0


class RunLockHeld(Exception):
    """Another process holds the update run lock."""


def new_run_id() -> str:
    return secrets.token_hex(16)


def validate_run_id(run_id: str) -> bool:
    return RUN_ID_RE.fullmatch(run_id) is not None


def _lock_path(env: dict[str, str] | None) -> Path:
    return state_dir("trailhead", env=env) / _LOCK_FILENAME


def acquire_run_lock(run_id: str, *, env: dict[str, str] | None = None) -> int:
    """Take the run lock and record ``{run_id, pid, started_at}``.

    Returns the lock descriptor; closing it (see :func:`release_run_lock`)
    drops the lock. Raises :class:`RunLockHeld` without touching the file's
    contents when another process holds it.
    """
    path = _lock_path(env)
    ensure_dir(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.set_inheritable(fd, False)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunLockHeld(str(path)) from None
        record = {
            "run_id": run_id,
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(record).encode())
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


def release_run_lock(fd: int) -> None:
    os.close(fd)


def _parse_record(raw: str) -> dict | None:
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if (
        isinstance(data, dict)
        and isinstance(data.get("run_id"), str)
        and isinstance(data.get("pid"), int)
        and isinstance(data.get("started_at"), str)
    ):
        return {k: data[k] for k in ("run_id", "pid", "started_at")}
    return None


def read_lock_record(*, env: dict[str, str] | None = None) -> dict | None:
    """The ``{run_id, pid, started_at}`` the lock file last recorded, whether
    or not anyone still holds the lock. ``None`` when there is no usable record."""
    try:
        raw = _lock_path(env).read_text()
    except FileNotFoundError:
        return None
    return _parse_record(raw)


def current_holder(*, env: dict[str, str] | None = None) -> dict | None:
    """The record of the process holding the run lock right now, or ``None``.

    Probes with a shared non-blocking lock attempt, so a lock file left behind
    by a killed process reads as unheld.
    """
    try:
        fd = os.open(_lock_path(env), os.O_RDONLY)
    except FileNotFoundError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            return None
        deadline = time.monotonic() + _RECORD_WAIT_SECONDS
        while True:
            record = read_lock_record(env=env)
            if record is not None or time.monotonic() >= deadline:
                return record
            time.sleep(0.02)
    finally:
        os.close(fd)


def build_status(*, env: dict[str, str] | None = None) -> dict:
    """The ``trailhead update --status --json`` object (status schema 1).
    Read-only: no git, no network."""
    return {
        "status_schema_version": STATUS_SCHEMA_VERSION,
        "managed_outpost": outpost_lifecycle.managed_outpost(env=env),
        "running": current_holder(env=env),
        "last_result": None,
    }
