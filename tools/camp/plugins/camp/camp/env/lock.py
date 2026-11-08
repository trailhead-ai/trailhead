"""The lock every `camp env attach` by one user on one machine meets on.

Guards the decide-and-start window of an attach: whoever holds it reads the process
table and starts the forward without another attach doing the same in between. It is
an advisory exclusive `flock` on a single file under camp's state directory, and it
records nothing: the file stays empty, and the holder's identity is never written.

The file is never unlinked. Removing it while attachers are queued lets one open the
old inode and another create a new one, so two holders lock different files and
exclude nothing. The kernel releases the lock when the holder's descriptor closes,
including when the holder dies. The descriptor is opened close-on-exec, so no child
the holder spawns keeps the lock alive after the holder is gone.

The bounded `LOCK_NB` polling loop deliberately mirrors the one in
`camp/group/manifest.py` rather than sharing it, so this module does not reach into
the group package.
"""
from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

_POLL_INTERVAL_SECONDS = 0.05


class LockBusy(Exception):
    """The lock stayed held by another process for the whole timeout."""


class LockUnavailable(Exception):
    """The lock file cannot be opened; `path` and `reason` say where and why."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"camp env: cannot open attach lock {path}: {reason}")
        self.path = path
        self.reason = reason


def attach_lock_path() -> Path:
    from trailhead.paths import state_dir

    return state_dir("camp") / "env-attach.lock"


def _open(path: Path) -> int:
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        raise LockUnavailable(path, exc.strerror or str(exc)) from exc


@contextmanager
def attach_lock(
    path: Path | str,
    *,
    timeout: float,
    on_wait: Callable[[], None] | None = None,
) -> Iterator[None]:
    """Hold the exclusive lock at *path* for the body of the `with`.

    Polls non-blocking for up to *timeout* seconds, then raises `LockBusy`. *on_wait*
    is called once, only if the first try finds the lock held. A `KeyboardInterrupt`
    while waiting propagates after the descriptor is closed.
    """
    path = Path(path)
    fd = _open(path)
    try:
        deadline = time.monotonic() + timeout
        waited = False
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not waited:
                    waited = True
                    if on_wait is not None:
                        on_wait()
                if time.monotonic() >= deadline:
                    raise LockBusy(f"camp env: attach lock {path} is held by another attach") from None
                time.sleep(_POLL_INTERVAL_SECONDS)
        yield
    finally:
        os.close(fd)
