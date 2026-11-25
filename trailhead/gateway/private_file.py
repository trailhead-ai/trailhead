"""Reading a file Outpost wrote for the operator alone, the one way the gateway commands do it."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path


class Unsafe(Exception):
    """The file is not what Outpost writes: the message says why, and the contents were never read."""


def read_private_file(path: Path, what: str, operator_uid: int) -> str:
    """The text of *path* (called *what* in a refusal), which must be a regular file *operator_uid* owns with mode 0600.

    It is opened without following a link and checked on the open descriptor before a byte is read.
    Raises ``FileNotFoundError`` when there is no file, :class:`Unsafe` for a file that is the wrong kind,
    owner or mode, and ``OSError`` for any other failure to open or read it."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as err:
        if err.errno == errno.ELOOP:
            raise Unsafe(f"the {what} {path} is a symbolic link; refusing to read it") from None
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise Unsafe(f"the {what} {path} is not a regular file; refusing to read it")
        if info.st_uid != operator_uid:
            raise Unsafe(f"the {what} {path} is not owned by you; refusing to read it")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise Unsafe(
                f"the {what} {path} has mode {stat.S_IMODE(info.st_mode):04o}; it must be 0600, refusing to read it"
            )
        with os.fdopen(fd, "r") as handle:
            fd = -1
            return handle.read()
    finally:
        if fd != -1:
            os.close(fd)
