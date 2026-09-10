"""Workspace-scoped conversation enumeration for the transfer preflight.

`camp transfer`'s dry-run has to name which conversations would cross with a
workspace and which would not, without moving or reading anything new: the
answer is composed entirely over the same fail-closed pool `camp remove`'s
teardown guard already builds — :func:`launch.recovery.session_candidates` for
the dedup, :class:`launch.teardown_guard.EnumerationUnavailable` for the
fail-closed contract. Nothing here starts a process or reads a harness itself;
the pool is injected by the caller, exactly as
:func:`launch.teardown_guard.blocking_sessions` takes it, so this module stays
pure data-to-data and the CLI edge owns gathering the pool and rendering the
answer.

THIS IS NOT `blocking_sessions`. That function drops a candidate whose root is
unreadable or missing, because "nothing left to resume there" is the right
answer for a destructive removal. A transfer preflight asks a different
question — "what would this operation cross, or fail to account for" — and a
silently dropped conversation understates that. So a candidate whose recorded
root cannot be read at all is reported here, not dropped, as UNRESOLVED.

Each row is a :class:`WorkspaceConversation`, one of exactly three outcomes:

- ROOTED HERE — the recorded root is the workspace itself or a subtree of it.
  ``subpath`` carries the location relative to the workspace root, as a
  ``PurePosixPath`` — ``PurePosixPath(".")`` when the root IS the workspace
  root. ``unresolved`` is ``False``.
- UNRESOLVED — the harness could not tell camp where the session ran at all
  (:attr:`SessionCandidate.unreadable`). ``subpath`` is ``None`` and
  ``unresolved`` is ``True``. Never conflated with "rooted here": a caller
  cannot report where an unresolved conversation sits, only that it exists.
- EXCLUDED — the recorded root lies outside the workspace. This outcome has no
  row at all; a caller counts what is absent from the result, not a third
  field on it.

``live`` carries the candidate's own liveness flag unchanged, so a still-running
session is never collapsed into "would cross" without saying it is live.

A session present in both the transcript and live-record pools is exactly one
row — `session_candidates` already keys by session id and nothing else, so this
module does not re-derive the dedupe itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from ..launch.recovery import session_candidates
from ..launch.teardown_guard import EnumerationUnavailable

__all__ = ["WorkspaceConversation", "workspace_conversations", "EnumerationUnavailable"]


@dataclass(frozen=True)
class WorkspaceConversation:
    """One conversation the preflight found while scoping to a workspace.

    See the module docstring for the three-outcome shape ``subpath`` and
    ``unresolved`` together encode.
    """

    session_id: str
    subpath: PurePosixPath | None
    live: bool
    unresolved: bool


def workspace_conversations(
    workspace: Path | str,
    *,
    transcripts: Iterable[Any] | None,
    live_records: Iterable[Any] | None,
    groups: Iterable[dict[str, Any]],
    env: Mapping[str, str],
    now: datetime | None = None,
) -> tuple[WorkspaceConversation, ...]:
    """Every conversation rooted in *workspace*, plus every unresolved one.

    ``None`` for either pool is the unanswerable case and raises
    :class:`EnumerationUnavailable`; it is never read as an empty pool — the
    same rule :func:`launch.teardown_guard.blocking_sessions` holds, for the
    same reason: an unenumerable seam must never look like "nothing here".
    """
    if transcripts is None or live_records is None:
        raise EnumerationUnavailable(
            "camp could not enumerate this harness's sessions, so it cannot "
            "tell which conversations are rooted in this workspace"
        )

    root = Path(workspace).resolve()
    rows: list[WorkspaceConversation] = []
    for candidate in session_candidates(
        transcripts=transcripts,
        live_records=live_records,
        groups=groups,
        env=env,
        now=now,
    ):
        if candidate.unreadable:
            rows.append(
                WorkspaceConversation(
                    session_id=candidate.session_id,
                    subpath=None,
                    live=candidate.live,
                    unresolved=True,
                )
            )
            continue

        resolved = candidate.root.resolve()
        if resolved == root:
            subpath = PurePosixPath(".")
        elif resolved.is_relative_to(root):
            subpath = PurePosixPath(resolved.relative_to(root).as_posix())
        else:
            continue

        rows.append(
            WorkspaceConversation(
                session_id=candidate.session_id,
                subpath=subpath,
                live=candidate.live,
                unresolved=False,
            )
        )

    return tuple(rows)
