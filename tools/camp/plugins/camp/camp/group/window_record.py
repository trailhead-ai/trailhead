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

from .manifest import LockTimeout, reconcile_lock

WINDOW_RECORD_FILENAME = "windows.json"

#: The one schema version `write_window_record` ever stamps and
#: `_read_window_record_unlocked` ever accepts. A record carrying any other
#: value (or none at all) is a version this camp cannot safely interpret —
#: three later slices read and extend this format, so silently reading an
#: unrecognised version as if it were this one is the exact fail-open shape
#: `WindowRecordRead.status` exists to avoid.
_SCHEMA_VERSION = 1


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
        normalized = os.path.normpath(self.cwd)
        if normalized == os.pardir or normalized.startswith(os.pardir + os.sep):
            raise WindowRecordError(
                f"camp: window entry cwd must stay inside the workspace, got "
                f"escaping path {self.cwd!r}"
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
        "schema_version": _SCHEMA_VERSION,
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

    if data.get("schema_version") != _SCHEMA_VERSION:
        raise WindowRecordError(
            f"camp: window record at {path} has unrecognised schema_version "
            f"{data.get('schema_version')!r} (expected {_SCHEMA_VERSION!r})"
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
        # A path that is present but NOT a regular file — a directory, or a
        # dangling symlink (present via lstat, absent via the stat this
        # `exists()` follows) — is a genuinely unknown state, not the
        # permissive "no session here yet" one: camp cannot tell what is
        # there, so it must not inherit "missing"'s fail-open answer.
        if path.exists() or path.is_symlink():
            return WindowRecordRead(
                status="corrupt",
                entries=(),
                error=f"camp: window record at {path} exists but is not a regular file",
            )
        return WindowRecordRead(status="missing", entries=())
    try:
        entries = _read_window_record_unlocked(path)
    except WindowRecordError as e:
        return WindowRecordRead(status="corrupt", entries=(), error=str(e))
    return WindowRecordRead(status="ok", entries=tuple(entries))


def record_window_entry_unlocked(path: Path, entry: WindowEntry) -> None:
    """Read-modify-write *entry* into the record WITHOUT acquiring the
    workspace lock: it replaces, in place, the entry already recorded for
    the same `window_id`, or is appended when that window has none.

    One entry per window, because a window holds one conversation at a
    time — a new conversation started in it (a fresh launch, a resume, a
    cleared context) supersedes the one recorded before, and resurrecting
    both would bring back a window the operator had already moved on from.

    The caller MUST already hold the workspace's reconcile_lock — mirrors
    flip_member_state_unlocked's contract (camp/group/manifest.py:390-396).
    Re-acquiring flock on a second fd in the same process blocks forever,
    so this unlocked primitive is the only write path available to a
    caller already inside `with reconcile_lock(ws_dir): ...`.
    """
    entries = _read_window_record_unlocked(path)
    for index, existing in enumerate(entries):
        if existing.window_id == entry.window_id:
            entries[index] = entry
            break
    else:
        entries.append(entry)
    write_window_record(path, entries)


def record_window_entry(
    ws_dir: Path, entry: WindowEntry, *, lock_timeout: float | None = None
) -> None:
    """Acquire the workspace lock and record *entry* — see
    :func:`record_window_entry_unlocked` for the one-entry-per-window rule.

    Serializes on the same workspace-scoped lock manifest mutations use
    (`reconcile_lock`), so a concurrent pair of writers under the same
    ws_dir never race a read-modify-write and lose an entry. *lock_timeout*
    bounds the wait for that lock; `LockTimeout` propagates when it runs
    out, with the record untouched. ``None`` waits indefinitely.
    """
    ws_dir = Path(ws_dir)
    path = window_record_path_for(ws_dir)
    with reconcile_lock(ws_dir, timeout=lock_timeout):
        record_window_entry_unlocked(path, entry)


@dataclass(frozen=True)
class Restamped:
    """`restamp_window_entries` succeeded; `entries` is the record's new
    content, in the order written."""

    entries: tuple[WindowEntry, ...]


@dataclass(frozen=True)
class NotRestamped:
    """`restamp_window_entries` could not write — a lock timeout or a
    corrupt record at restamp time. `reason` names the record path."""

    reason: str


def restamp_window_entries(
    ws_dir: Path,
    mapping: dict[str, WindowEntry],
    *,
    remove: set[str],
    lock_timeout: float | None,
) -> Restamped | NotRestamped:
    """Re-stamp the window record after resurrection, as one locked
    read-modify-write.

    Knows nothing about resurrection itself — it is the record
    read-modify-write resurrection's engine calls once its per-window
    plan is settled, mirroring record_window_entry's locked-wrapper shape.

    *mapping* is old `window_id` -> replacement `WindowEntry` (same
    logical entry, tmux's newly assigned id, and tmux's read-back name).
    *remove* is the set of old ids to drop. Every entry not named in
    either — including one appended to the record after the caller's plan
    was computed — is carried forward unchanged, in its original
    position, so a concurrent appender's write is never lost.

    Under `reconcile_lock(ws_dir, timeout=lock_timeout)`: read the
    record, apply the plan, write atomically. A lock that cannot be
    acquired within *lock_timeout*, or a record that cannot be parsed at
    restamp time (unreachable on the door's path, which already refused
    an unparseable record before resurrection began — the primitive stays
    honest about it regardless), returns `NotRestamped` naming the record
    path rather than raising. An `OSError` from acquiring the lock or from
    the atomic write itself (e.g. `ENOSPC`) folds the same way — the
    caller already treats `NotRestamped` as "the resurrection still
    counts, the record just didn't get re-stamped"; letting an `OSError`
    escape instead would surface as a false create failure even though
    tmux already holds the resurrected session.
    """
    ws_dir = Path(ws_dir)
    path = window_record_path_for(ws_dir)
    try:
        with reconcile_lock(ws_dir, timeout=lock_timeout):
            try:
                entries = _read_window_record_unlocked(path)
            except WindowRecordError as e:
                return NotRestamped(reason=str(e))

            restamped: list[WindowEntry] = []
            for entry in entries:
                if entry.window_id in mapping:
                    restamped.append(mapping[entry.window_id])
                elif entry.window_id in remove:
                    continue
                else:
                    restamped.append(entry)

            write_window_record(path, restamped)
            return Restamped(entries=tuple(restamped))
    except LockTimeout as e:
        return NotRestamped(reason=f"camp: could not restamp window record at {path}: {e}")
    except OSError as e:
        return NotRestamped(reason=f"camp: could not restamp window record at {path}: {e}")
