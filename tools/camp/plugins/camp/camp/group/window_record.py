"""Per-workspace window record: read/write/lock for what is inside a
workspace's tmux session.

The window record lives beside the provisioning manifest, in the same
workspace dir:
    central_state_dir(group)/worktrees/<slug>/windows.json

It mirrors manifest.py's conventions rather than inventing a second one:

  - Atomic write: mkstemp in the destination dir, write, os.replace,
    chmod 0o600, unlink the temp on any failure
    (camp/group/manifest.py:159-176).
  - Locked / `_unlocked` entry-point split: `reconcile_lock` is not
    reentrant (camp/group/manifest.py:390-396 — re-acquiring flock on a
    second fd in the same process blocks forever), so every read-modify-
    write exposes an `_unlocked` primitive for a caller that already holds
    the workspace lock, plus a locked wrapper for everyone else.

Schema (v1):
    {
        "schema_version": 1,
        "windows": [
            {
                "window_id": "<tmux window id, e.g. '@3'>",
                "name": "<window name>",
                "cwd": "<workspace-relative working directory>",
                "conversation_id": "<claude conversation id>" | null,
                "command_line": "<shell command>" | null,
            },
            ...
        ],
    }

Exactly one of conversation_id / command_line is ever non-null on a given
entry — enforced at WindowEntry construction, so an invalid entry is
rejected before it ever reaches a write.

Reader contract (load-bearing for later slices — see
`WindowRecordRead.status`): a missing record file is a normal, expected
state (a workspace whose session has not been created yet), NOT an error,
and reads as an empty, "ok" record. A present-but-unreadable file (bad
JSON, truncated content, unexpected shape) is a genuinely different state
— "corrupt" — and is reported via the same non-throwing return value
rather than an exception, so a caller that does not care about window
state (status, path, activate, remove) never has to import this module's
exception type just to stay resilient to a broken record.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manifest import reconcile_lock

WINDOW_RECORD_FILENAME = "windows.json"


class WindowRecordError(Exception):
    """Raised for an invalid window entry, or internally for a malformed
    record file — never raised by the public `read_window_record` reader,
    which reports malformed content via `WindowRecordRead.status` instead.
    """


def window_record_path_for(ws_dir: Path) -> Path:
    """Return the per-workspace window record path, a sibling of
    manifest.json inside ws_dir."""
    return Path(ws_dir) / WINDOW_RECORD_FILENAME


@dataclass(frozen=True)
class WindowEntry:
    """One recorded tmux window.

    `cwd` is always workspace-relative — never absolute (AC29) — and
    validated as such at construction, so an absolute path can never reach
    a write. Exactly one of `conversation_id` / `command_line` is set.
    """

    window_id: str
    name: str
    cwd: str
    conversation_id: str | None = None
    command_line: str | None = None

    def __post_init__(self) -> None:
        if Path(self.cwd).is_absolute():
            raise WindowRecordError(
                f"camp: window entry cwd must be workspace-relative, got "
                f"absolute path {self.cwd!r}"
            )
        has_conversation = self.conversation_id is not None
        has_command = self.command_line is not None
        if has_conversation == has_command:
            raise WindowRecordError(
                "camp: window entry must carry exactly one of "
                "conversation_id or command_line"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "name": self.name,
            "cwd": self.cwd,
            "conversation_id": self.conversation_id,
            "command_line": self.command_line,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WindowEntry":
        return cls(
            window_id=data["window_id"],
            name=data["name"],
            cwd=data["cwd"],
            conversation_id=data.get("conversation_id"),
            command_line=data.get("command_line"),
        )


@dataclass(frozen=True)
class WindowRecordRead:
    """Result of a non-throwing window record read.

    status:
        "ok"      — the file was read and parsed; `entries` reflects it
                    (possibly empty).
        "missing" — no record file exists yet; `entries` is always empty.
                    Expected state for a workspace whose session has not
                    been created.
        "corrupt" — the file exists but could not be parsed as a valid
                    record (malformed JSON, truncated write, unexpected
                    shape); `entries` is always empty and `error` names
                    the reason.
    """

    status: str
    entries: tuple[WindowEntry, ...] = ()
    error: str | None = None


def write_window_record(path: Path, entries: list[WindowEntry]) -> None:
    """Write entries to path atomically with mode 0o600.

    Mirrors write_central_manifest's temp-then-rename discipline
    (camp/group/manifest.py:159-176): mkstemp in the destination dir,
    write, os.replace, chmod 0o600, unlink the temp on any failure — so a
    write that fails midway leaves the previous record intact and no temp
    file behind (AC31).
    """
    path = Path(path)
    data = {
        "schema_version": 1,
        "windows": [e.to_dict() for e in entries],
    }

    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".windows-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, str(path))
        os.chmod(str(path), 0o600)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _read_window_record_unlocked(path: Path) -> list[WindowEntry]:
    """Read and parse the window record at path.

    A missing file reads as an empty list (no exception — the caller
    handles the missing-vs-corrupt distinction). Any other read/parse
    failure raises WindowRecordError.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as e:
        raise WindowRecordError(f"camp: cannot read window record at {path}: {e}") from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise WindowRecordError(f"camp: malformed window record at {path}: {e}") from e

    if not isinstance(data, dict) or not isinstance(data.get("windows"), list):
        raise WindowRecordError(
            f"camp: window record at {path} is not the expected shape"
        )

    try:
        return [WindowEntry.from_dict(item) for item in data["windows"]]
    except (KeyError, TypeError, WindowRecordError) as e:
        raise WindowRecordError(f"camp: malformed window record at {path}: {e}") from e


def read_window_record(path: Path) -> WindowRecordRead:
    """Read the window record at path, never raising.

    See WindowRecordRead's docstring for the three-way status contract
    this exists to give later callers (resurrection, reconciliation, and
    every command that does not need window state) a way to distinguish
    "no record yet" from "record exists but is broken" without having to
    catch an exception.
    """
    path = Path(path)
    if not path.is_file():
        return WindowRecordRead(status="missing", entries=())
    try:
        entries = _read_window_record_unlocked(path)
    except WindowRecordError as e:
        return WindowRecordRead(status="corrupt", entries=(), error=str(e))
    return WindowRecordRead(status="ok", entries=tuple(entries))


def append_window_entry_unlocked(path: Path, entry: WindowEntry) -> None:
    """Read-modify-write one window entry into the record WITHOUT
    acquiring the workspace lock.

    The caller MUST already hold the workspace's reconcile_lock — mirrors
    flip_member_state_unlocked's contract (camp/group/manifest.py:390-396).
    Re-acquiring flock on a second fd in the same process blocks forever,
    so this unlocked primitive is the only append path available to a
    caller already inside `with reconcile_lock(ws_dir): ...`.
    """
    entries = _read_window_record_unlocked(path)
    entries.append(entry)
    write_window_record(path, entries)


def append_window_entry(ws_dir: Path, entry: WindowEntry) -> None:
    """Acquire the workspace lock and append one window entry to its
    record.

    Serializes on the same workspace-scoped lock manifest mutations use
    (`reconcile_lock`), so a concurrent pair of writers under the same
    ws_dir never race a read-modify-write and lose an entry.
    """
    ws_dir = Path(ws_dir)
    path = window_record_path_for(ws_dir)
    with reconcile_lock(ws_dir):
        append_window_entry_unlocked(path, entry)
