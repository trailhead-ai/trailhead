"""Whether this process runs inside the harness's sandbox, read from what the sandbox does to Outpost's
credential folders and never from an environment variable or argument a session sets.

The folders probed are ``.local/state/outpost/<name>`` under the account's home in the password
database (`account_home`), not under `OUTPOST_STATE_DIR`, `XDG_STATE_HOME` or `HOME`, which a session
can set to point the probe at folders the sandbox does not hide. When none of the probed folders exists
the probe has nothing to read, so it fails closed and reports sandboxed.

On Linux the sandbox mounts a tmpfs over each of Outpost's read-denied folders (`operator`,
`registration`, `sessions`, `db` under Outpost's state directory), which `/proc/self/mountinfo` lists.
On macOS the sandbox refuses the read, so listing an existing folder raises `PermissionError`. A host
terminal sees neither.

A home behind a symlink (/home -> /var/home) is mounted over at its resolved path, so each folder is matched at both its
raw and its resolved path.

`MOUNTINFO` is the Linux file read and `account_home` the home lookup; a test points each at a fabricated one.
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path

FOLDERS = ("operator", "registration", "sessions", "db")
MOUNTINFO = Path("/proc/self/mountinfo")
STATE_UNDER_HOME = Path(".local") / "state" / "outpost"

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


def account_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _exists(folder: Path) -> bool:
    try:
        os.lstat(folder)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def is_sandboxed() -> bool:
    root = account_home() / STATE_UNDER_HOME
    folders = [root / name for name in FOLDERS]
    if not any(_exists(f) for f in folders):
        return True
    probed = {str(p) for p in folders} | {os.path.realpath(p) for p in folders}
    return _masked(probed) or _refused(folders)
