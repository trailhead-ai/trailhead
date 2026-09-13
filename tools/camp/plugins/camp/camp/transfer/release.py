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

**Idempotent per conversation, keyed on the live source, not the archive.**
Before touching a conversation, `release_conversations` always asks
*locate_transcript* whether a live copy still sits in this host's harness
store. Only when it does not — the ordinary case for a re-run after a
successful archive, since the earlier run already moved the file away — does
an existing archive destination short-circuit to `ALREADY_ARCHIVED` with
nothing further touched. A live copy that IS found is always relocated,
replacing any stale archive already at that destination. This is what a
round-tripped conversation needs: a conversation this host archived,
received back from the peer, and is now sending outward a second time has a
fresh live transcript sitting where the first archive's destination path
still exists from the earlier leg — checking the destination alone would
report `ALREADY_ARCHIVED` and leave that fresh copy sitting resumable on
this host while the peer believes it now owns the only copy. Checking the
source first closes that: a live copy is never left behind merely because
some earlier transfer once archived this session id.

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
happened) or `FAILED` (nothing moved). The append's own read-modify-write
is serialized by the workspace's `reconcile_lock` (see `_append_marker`),
the same lock `flip_sender_ownership` below takes on the same workspace —
this call site is not the marker's only writer (a post-commit failure
branch archives too), so the exclusion is load-bearing, not defensive. A
write that cannot take the lock is reported `FAILED` for that
conversation rather than silently dropping the entry or corrupting the
file — with `archive_path` still naming the destination the relocation
already landed at, since that move happened before the marker append was
even attempted; only a `FAILED` outcome whose relocation itself never
happened carries `archive_path=None`.

**`flip_sender_ownership` is the sender's last write of the whole verb.**
Called by the CLI unconditionally once `release_conversations` has
returned — regardless of whether every crossed conversation's own release
came back `ARCHIVED`: a per-conversation `FAILED` outcome is reported to
the operator, never treated as a reason to withhold the flip, since
ownership has already moved to the peer by the time either function runs.
It writes *this* host's own manifest to name the peer as owner, using the
exact name `camp.transfer.move.MoveResult.claimed_owner` carries (the
peer's own declared name, never the sender's `--to` alias for it). Like
`camp.transfer.receive.claim`'s write on the peer side, it goes through
`write_central_manifest`'s guarded `allow_owner_change=True` opt-in — the
same bypass-proof gate every deliberate ownership change uses — under the
same `reconcile_lock` every other manifest mutation on this workspace
takes. A workspace that never recorded an owner is not a special case: the
guard accepts any write when the on-disk manifest carries no owner,
opt-in or not, so this call is identical either way.
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
    "flip_sender_ownership",
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


def _append_marker(
    root: Path, entry: dict[str, Any], *, group: str, slug: str, env: dict[str, str] | None = None
) -> None:
    """Append one entry to the archive's durable marker. Called only
    immediately after the move it records has already succeeded.

    The read-modify-write is wrapped in the SAME `reconcile_lock` this
    workspace's manifest write already takes (see `flip_sender_ownership`
    below) — keyed on `workspace_dir(group, slug)`, never on the archive
    root itself, so a marker append and a manifest mutation on the same
    workspace serialize on the one lock a workspace owns. This is what
    makes a second concurrent append survive rather than losing an entry
    through the unlocked window this function used to have.
    """
    from ..group.manifest import reconcile_lock, workspace_dir

    ws_dir = workspace_dir(group, slug, env=env)
    with reconcile_lock(ws_dir):
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


def _move_aside_and_replace(dest: Path, source: Path) -> None:
    """Relocate *source* onto *dest*, never by deleting a pre-existing
    *dest* up front.

    A stale *dest* (a file or a directory — this is used for both the
    top-level transcript and the nested subtree) is moved aside to a sibling
    path first; only once *source* has landed at *dest* is the stale copy
    actually discarded. A failure moving *source* in restores the stale copy
    exactly, so a failed replacement never leaves the operator with less
    than they started with — no archive at all, instead of the prior one.
    """
    backup = dest.with_name(f".{dest.name}.stale")
    had_prior = dest.exists()
    if had_prior:
        if backup.exists():
            if backup.is_dir():
                shutil.rmtree(backup)
            else:
                backup.unlink()
        shutil.move(str(dest), str(backup))
    try:
        shutil.move(str(source), str(dest))
    except OSError:
        if had_prior:
            shutil.move(str(backup), str(dest))
        raise
    if had_prior:
        if backup.is_dir():
            shutil.rmtree(backup)
        else:
            backup.unlink()


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

        conversation_root = (
            workspace_root
            if str(conversation.subpath) == "."
            else workspace_root.joinpath(*conversation.subpath.parts)
        )
        transcript_path = locate_transcript(session_id, conversation_root)

        if transcript_path is None:
            if dest.exists():
                results.append(
                    ConversationRelease(
                        session_id=session_id,
                        outcome=ReleaseOutcome.ALREADY_ARCHIVED,
                        archive_path=dest,
                    )
                )
            else:
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

        # The top-level transcript is relocated FIRST, never the nested
        # subtree — `archive_path` is this loop's only signal of what
        # actually moved, and only the top-level file's destination is ever
        # reported as `archive_path`. Relocating it first means a failure
        # from here on always has a truthful `archive_path` to report:
        # `None` while nothing has moved yet, `dest` once it has, in either
        # order the subsequent nested-subtree step can fail.
        try:
            root.mkdir(parents=True, exist_ok=True)
            _move_aside_and_replace(dest, transcript_path)
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

        if nested_source.is_dir():
            try:
                _move_aside_and_replace(nested_dest, nested_source)
            except OSError as e:
                results.append(
                    ConversationRelease(
                        session_id=session_id,
                        outcome=ReleaseOutcome.FAILED,
                        archive_path=dest,
                        detail=(
                            "the top-level transcript was archived but its "
                            f"nested subagent/tool-result subtree could not "
                            f"be relocated: {e}"
                        ),
                    )
                )
                continue

        try:
            _append_marker(
                root,
                {
                    "session_id": session_id,
                    "archive_path": str(dest),
                    "at": datetime.now(timezone.utc).isoformat(),
                },
                group=group,
                slug=slug,
                env=env,
            )
        except OSError as e:
            # The relocation above has already succeeded by this point — the
            # transcript is no longer at its source, only the marker append
            # failed — so `archive_path` names where it actually landed
            # rather than `None`, which the CLI's renderer reserves for
            # "nothing moved". See `_render_move_completion`'s FAILED
            # handling in `camp.cli.transfer`.
            results.append(
                ConversationRelease(
                    session_id=session_id,
                    outcome=ReleaseOutcome.FAILED,
                    archive_path=dest,
                    detail=f"archived but could not record the durable marker: {e}",
                )
            )
            continue

        results.append(
            ConversationRelease(
                session_id=session_id, outcome=ReleaseOutcome.ARCHIVED, archive_path=dest
            )
        )

    return tuple(results)


def flip_sender_ownership(
    *, group: str, slug: str, owner: str, env: dict[str, str] | None = None
) -> None:
    """Write this host's own manifest to name *owner* as the workspace's
    owner — the sender's last write of the whole verb. See the module
    docstring for why this must run only after `release_conversations` has
    finished archiving and marking every crossed conversation, and why the
    guarded `allow_owner_change=True` opt-in is used unconditionally.
    """
    from ..group.manifest import (
        manifest_path_for,
        read_central_manifest,
        reconcile_lock,
        workspace_dir,
        write_central_manifest,
    )

    mpath = manifest_path_for(group, slug, env=env)
    ws_dir = workspace_dir(group, slug, env=env)

    with reconcile_lock(ws_dir):
        data = read_central_manifest(mpath)
        data["owner"] = owner
        write_central_manifest(mpath, data, allow_owner_change=True)
