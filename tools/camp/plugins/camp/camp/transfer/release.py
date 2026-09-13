"""`camp.transfer.release` — the sender's own post-claim release step.

Once `camp.transfer.move.move_workspace` has returned with a populated
`claimed_owner` (the peer has confirmed it owns the workspace — see that
module's docstring), the sender still holds its own resumable copy of every
conversation that just crossed. This module moves each one out of the
harness's transcript store into a camp-owned archive, so the sender no
longer offers or resumes it.

**Locating a conversation is never re-derived here.** Exactly like
`camp.transfer.conversations.send_workspace_conversations`, this module is
handed a *locate_transcript* callable shaped like
`Harness.session_transcript_path` — `(session_id, conversation_root) -> Path
| None` — and never learns the projects-directory munge rule itself; that
stays the harness boundary's alone (see
`tools/camp/tests/test_projects_key_confinement.py`).

**The archive is camp's own path, keyed by session id alone.** `archive_dir`
resolves to `central_state_dir(group)/transfer-archive/<slug>` — a sibling of
`central_state_dir(group)/worktrees/<slug>` (the workspace tree, see
`camp.group.manifest.workspace_dir`), never beneath it, so a later
transfer's working-tree phase (`camp.transfer.worktree.write_archive`, which
walks the worktree directory itself) can never sweep it up as untracked
content. It sits under `CAMP_STATE_DIR`, an entirely different root from the
harness's own transcript store (`TRAILHEAD_CLAUDE_DIR`), so conversation
enumeration (`camp.launch.recovery.session_candidates`, which reads only the
harness's own store) never reports it either. Every archived file is named
`<session-id>.jsonl` (plus a `<session-id>/` sibling directory for any
nested subagent/tool-result files) — keyed on the session id alone, which is
already the harness's own global uniqueness key, never on a munged
workspace path. Two conversations that share a subpath (or whose source
workspace paths would munge to the same key under the harness's own lossy
`_projects_key` rule) still land at two distinct archive paths, because
nothing about that munge is ever re-derived or reused here.

**Idempotent per conversation.** Before touching a conversation,
`release_conversations` checks whether its archive destination already
exists; if so, it reports `ALREADY_ARCHIVED` and touches nothing further —
*locate_transcript* is not even consulted — so a re-run after a partial
release never duplicates an already-archived transcript and never reports
one as newly archived.

**Relocation uses `shutil.move`, not a bare rename.** A camp-owned archive
and the harness's own transcript store are not guaranteed to share a
filesystem; `shutil.move` falls back to a copy-then-remove when the direct
rename raises `OSError` (cross-device or otherwise), so a relocation across
a filesystem boundary succeeds the same way a same-filesystem one does,
without this module special-casing it.

**Per-conversation failure, never a phase-wide one.** A conversation whose
relocation fails (an unwritable destination, a source that has vanished) is
reported as `FAILED` with a `detail` message naming why; every other
conversation is still attempted and reported on its own. The workspace's
ownership has already moved to the peer by the time this module runs — there
is no "abort the whole verb" outcome left to have, only an operator-visible
per-conversation report.

**A durable marker, appended only after a successful move.** Every
`ARCHIVED` outcome appends one entry — session id, archive path, timestamp —
to `<archive_dir>/.release-marker.json`, read back by
`read_release_marker`. Never written for `ALREADY_ARCHIVED` (nothing new
happened) or `FAILED` (nothing moved).
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable

from .move import ConversationCrossed

__all__ = [
    "ReleaseOutcome",
    "ConversationRelease",
    "archive_dir",
    "release_conversations",
    "read_release_marker",
]

_MARKER_FILENAME = ".release-marker.json"


class ReleaseOutcome(Enum):
    ARCHIVED = "archived"
    ALREADY_ARCHIVED = "already_archived"
    FAILED = "failed"


@dataclass(frozen=True)
class ConversationRelease:
    """One conversation's outcome from a single `release_conversations` call."""

    session_id: str
    outcome: ReleaseOutcome
    archive_path: Path | None
    detail: str | None = None


def archive_dir(group: str, slug: str, *, env: dict[str, str] | None = None) -> Path:
    """The camp-owned archive root for one workspace's released conversations.

    See the module docstring for why this is a sibling of, and never beneath,
    `camp.group.manifest.workspace_dir`'s own `worktrees/<slug>`.
    """
    from ..group.resolve import central_state_dir, validate_workspace_slug

    validate_workspace_slug(slug)
    return central_state_dir(group, env=env) / "transfer-archive" / slug


def _marker_path(root: Path) -> Path:
    return root / _MARKER_FILENAME


def _append_marker(root: Path, entry: dict[str, Any]) -> None:
    """Append one entry to the archive's durable marker. Called only
    immediately after the move it records has already succeeded."""
    root.mkdir(parents=True, exist_ok=True)
    path = _marker_path(root)

    existing: list[dict[str, Any]] = []
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                existing = raw
        except (OSError, json.JSONDecodeError):
            existing = []

    existing.append(entry)

    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(existing), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def read_release_marker(
    group: str, slug: str, *, env: dict[str, str] | None = None
) -> tuple[dict[str, Any], ...]:
    """Read back the durable marker for one workspace's released
    conversations, oldest entry first.

    Returns an empty tuple when no marker exists (nothing has been archived
    yet) or when it cannot be parsed — the marker is diagnostic, never
    load-bearing.
    """
    path = _marker_path(archive_dir(group, slug, env=env))
    if not path.is_file():
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(raw, list):
        return ()
    return tuple(raw)


def release_conversations(
    *,
    group: str,
    slug: str,
    workspace_root: Path,
    conversations: Iterable[ConversationCrossed],
    locate_transcript: Callable[[str, Path], Path | None],
    env: dict[str, str] | None = None,
) -> tuple[ConversationRelease, ...]:
    """Move every crossed conversation's transcript out of this host's
    harness store into its camp-owned archive.

    *conversations* is exactly the pool `move_workspace` reports as crossed
    (`MoveResult.conversations`) — never re-enumerated here. A caller
    passing an empty pool gets an empty result and no directory is created;
    see the module docstring for the idempotency, cross-filesystem, and
    per-conversation-failure contracts.
    """
    conversations = tuple(conversations)
    if not conversations:
        return ()

    root = archive_dir(group, slug, env=env)
    results: list[ConversationRelease] = []

    for conversation in conversations:
        session_id = conversation.session_id
        dest = root / f"{session_id}.jsonl"
        nested_dest = root / session_id

        if dest.exists():
            results.append(
                ConversationRelease(
                    session_id=session_id,
                    outcome=ReleaseOutcome.ALREADY_ARCHIVED,
                    archive_path=dest,
                )
            )
            continue

        conversation_root = (
            workspace_root
            if str(conversation.subpath) == "."
            else workspace_root.joinpath(*conversation.subpath.parts)
        )
        transcript_path = locate_transcript(session_id, conversation_root)
        if transcript_path is None:
            results.append(
                ConversationRelease(
                    session_id=session_id,
                    outcome=ReleaseOutcome.FAILED,
                    archive_path=None,
                    detail="transcript could not be located on this host",
                )
            )
            continue

        nested_source = transcript_path.parent / session_id

        try:
            root.mkdir(parents=True, exist_ok=True)
            if nested_source.is_dir():
                shutil.move(str(nested_source), str(nested_dest))
            shutil.move(str(transcript_path), str(dest))
        except OSError as e:
            results.append(
                ConversationRelease(
                    session_id=session_id,
                    outcome=ReleaseOutcome.FAILED,
                    archive_path=None,
                    detail=str(e),
                )
            )
            continue

        _append_marker(
            root,
            {
                "session_id": session_id,
                "archive_path": str(dest),
                "at": datetime.now(timezone.utc).isoformat(),
            },
        )
        results.append(
            ConversationRelease(
                session_id=session_id, outcome=ReleaseOutcome.ARCHIVED, archive_path=dest
            )
        )

    return tuple(results)
