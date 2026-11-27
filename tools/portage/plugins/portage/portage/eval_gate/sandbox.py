"""Whether this process runs inside the harness's sandbox, read from what the sandbox does to Outpost's
credential folders and never from an environment variable or argument a session sets.

On Linux the sandbox mounts a tmpfs over each of Outpost's read-denied folders (`operator`,
`registration`, `sessions`, `db` under Outpost's state directory), which `/proc/self/mountinfo` lists.
On macOS the sandbox refuses the read, so listing an existing folder raises `PermissionError`. A host
terminal sees neither.

`MOUNTINFO` is the Linux file read; a test points it at a fabricated one.
"""

from __future__ import annotations

import os
from pathlib import Path

from trailhead.paths import state_dir

FOLDERS = ("operator", "registration", "sessions", "db")
MOUNTINFO = Path("/proc/self/mountinfo")

MESSAGE = (
    "eval-gate: sandboxed\n"
    "this call runs inside the sandbox, which hides Outpost's credentials from eval and so does not let "
    "the eval gate run here. Nothing was run or pushed. Run the same command from a terminal on this "
    "host (outside any Claude Code session), then push again."
)


def _unescape(field: str) -> str:
    out, i = [], 0
    while i < len(field):
        if field[i] == "\\" and field[i + 1:i + 4].isdigit():
            out.append(chr(int(field[i + 1:i + 4], 8)))
            i += 4
        else:
            out.append(field[i])
            i += 1
    return "".join(out)


def _masked(folders: set[str]) -> bool:
    try:
        text = MOUNTINFO.read_text()
    except OSError:
        return False
    for line in text.splitlines():
        left, sep, right = line.partition(" - ")
        fields = left.split(" ")
        if sep and len(fields) > 4 and right.split(" ", 1)[0] == "tmpfs" and _unescape(fields[4]) in folders:
            return True
    return False


def _refused(folders: list[Path]) -> bool:
    for folder in folders:
        try:
            os.listdir(folder)
        except PermissionError:
            return True
        except OSError:
            continue
    return False


def is_sandboxed() -> bool:
    root = state_dir("outpost")
    folders = [root / name for name in FOLDERS]
    return _masked({str(p) for p in folders}) or _refused(folders)
