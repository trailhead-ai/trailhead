"""Process inspection for the harness seam: find the harness that owns this process.

``harness_process()`` walks the calling process's ancestors to the first one a
registered harness recognises as itself and returns ``(pid, start_time)``;
``session_key()`` turns that into the session identifier the Outpost gateway
registers under. Which executable counts as a harness is each
:class:`~trailhead.harness.base.Harness`'s answer (``recognizes_process``); nothing
here names a harness.

``start_time`` units match what Outpost verifies: on Linux the raw clock-tick
value of field 22 of ``/proc/<pid>/stat``; elsewhere integer epoch seconds parsed
from ``ps -o lstart=``. Anything that cannot be computed is ``None``, never an
exception.
"""

from __future__ import annotations

import os
import platform
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

Runner = Callable[[list[str]], str]

_MAX_DEPTH = 256


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    comm: str
    exe: str | None
    start_time: int


def parse_linux_stat(raw: str) -> tuple[int, int] | None:
    """``(ppid, start_time)`` from the text of ``/proc/<pid>/stat``, or ``None``.

    The command name (field 2) may contain spaces and parentheses, so fields are
    counted from after the LAST ``)``.
    """
    end = raw.rfind(")")
    if end < 0:
        return None
    rest = raw[end + 1 :].split()
    # rest[0] is field 3 (state); field 4 (ppid) is rest[1]; field 22 is rest[19].
    if len(rest) < 20:
        return None
    try:
        return int(rest[1]), int(rest[19])
    except ValueError:
        return None


def parse_lstart(text: str) -> int | None:
    """Epoch seconds from ``ps -o lstart=`` output (``Tue Nov  3 12:34:56 2026``)."""
    try:
        return int(time.mktime(time.strptime(" ".join(text.split()), "%a %b %d %H:%M:%S %Y")))
    except (ValueError, OverflowError):
        return None


def _read_linux(pid: int, proc_root: Path) -> ProcessInfo | None:
    try:
        parsed = parse_linux_stat((proc_root / str(pid) / "stat").read_text())
        comm = (proc_root / str(pid) / "comm").read_text().rstrip("\n")
        exe = os.readlink(proc_root / str(pid) / "exe")
    except OSError:
        return None
    if parsed is None:
        return None
    return ProcessInfo(pid, parsed[0], comm, exe, parsed[1])


def _read_ps(pid: int, runner: Runner) -> ProcessInfo | None:
    try:
        head = runner(["ps", "-o", "ppid=,comm=", "-p", str(pid)]).strip()
        lstart = runner(["ps", "-o", "lstart=", "-p", str(pid)])
        ppid_text, _, path = head.partition(" ")
        ppid = int(ppid_text)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    start = parse_lstart(lstart)
    path = path.strip()
    if start is None or not path:
        return None
    return ProcessInfo(pid, ppid, os.path.basename(path), os.path.realpath(path), start)


def _default_runner(args: list[str]) -> str:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
        env={**os.environ, "LC_ALL": "C"},
    ).stdout


def read_process(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
    system: str | None = None,
    runner: Runner | None = None,
) -> ProcessInfo | None:
    """Inspect one process, or ``None`` when it cannot be read."""
    if (system or platform.system()) == "Linux":
        return _read_linux(pid, proc_root)
    return _read_ps(pid, runner or _default_runner)


def harness_process(
    *,
    start_pid: int | None = None,
    proc_root: Path = Path("/proc"),
    system: str | None = None,
    runner: Runner | None = None,
) -> tuple[int, int] | None:
    """``(pid, start_time)`` of the nearest ancestor a harness recognises, or ``None``.

    Starts at ``start_pid`` (default: this process) and walks upward; the first
    recognised process wins, so a session nested inside another resolves to the
    inner one. An ancestor that cannot be read ends the walk with ``None``.
    """
    from trailhead.harness import _HARNESSES

    harnesses = [cls() for cls in _HARNESSES.values()]
    pid = os.getpid() if start_pid is None else start_pid
    seen: set[int] = set()
    while pid > 1 and pid not in seen and len(seen) < _MAX_DEPTH:
        seen.add(pid)
        info = read_process(pid, proc_root=proc_root, system=system, runner=runner)
        if info is None:
            return None
        if any(h.recognizes_process(info.comm, info.exe) for h in harnesses):
            return info.pid, info.start_time
        pid = info.ppid
    return None


def format_session_key(pid: int, start_time: int) -> str:
    """The session key Outpost registers a harness process under: ``ws-<pid>-<start_time>``."""
    return f"ws-{pid}-{start_time}"


def session_key(**kwargs: object) -> str | None:
    """The session key of the owning harness process, or ``None``."""
    found = harness_process(**kwargs)  # type: ignore[arg-type]
    if found is None:
        return None
    return format_session_key(*found)
