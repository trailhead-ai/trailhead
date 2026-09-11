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

---

The second half of this module is the SENDER side of the conversations
transfer channel: :func:`send_conversation` streams one conversation's
transcript, and :func:`send_workspace_conversations` drives it over every row
:func:`workspace_conversations` reported ROOTED HERE. The selection is never
re-derived — it iterates exactly the rows it is handed, so an EXCLUDED
conversation (no row at all) is never even visible to it, and an UNRESOLVED
row is refused by name (:class:`UnresolvedConversation`) rather than silently
dropped, so a workspace never half-crosses without saying so.

**Wire posture — carries no path from the sending host.** The remote argv
`send_conversation` invokes (`camp transfer-receive conversations ...`) names
only `--session-id` and `--subpath`, a workspace-relative
`PurePosixPath` rendered as a plain string (`"."` at the workspace root). No
absolute path, and no path fragment derived from this host's own directory
layout, ever crosses the wire — the peer resolves every destination from its
own group config, unchanged from every other phase's posture
(`transfer/worktree.py`, `transfer/receive.py`).

**Locating a conversation on disk is never re-derived here.** This module
does not know the projects-directory munge rule; it is handed an already
resolved *transcript_path* (`send_conversation`) or a *locate_transcript*
callable shaped exactly like `Harness.session_transcript_path`
(`send_workspace_conversations`) — the harness boundary stays the only place
that rule lives. What this module DOES know, because it was measured on the
real store rather than inferred: a conversation that dispatched a subagent or
produced tool-result artifacts owns a sibling directory named exactly its
session id, next to its own `<session-id>.jsonl`, holding every file this
conversation owns at any depth beneath it — `.jsonl` transcripts under
`subagents/`, and `.txt`/`.pdf`/`.json` artifacts under `tool-results/` that
carry no `.jsonl` at all. **The unit of transfer is that whole directory
tree, whatever it contains — not a filtered set of transcript files.**
Filtering by extension would both misdescribe the unit and require more code
than streaming the directory as-is. That sibling directory is "the
conversation's own directory" the test contract refers to — a nested file is
addressed on the wire by its path relative to THAT directory, never to the
shared `projects/<munged>/` directory one level up, and never by an absolute
path. A `memory/` directory sitting beside the session directories under
that same shared directory is project-scoped agent memory, not part of any
conversation, and is never reachable from a conversation's own directory —
it is excluded by construction, not by a name-based filter.

**Stream format.** The sender-side producer (`build_conversation_archive_argv`
runs this module itself as a standalone script, mirroring
`transfer/worktree.py`'s `build_archive_argv`) emits a `tarfile` stream
(`mode="w|"`) with exactly one member named `transcript.jsonl` — the
conversation's own top-level transcript — followed by every file under the
conversation's own directory, whatever its extension, each named by its path
relative to that directory (e.g. `subagents/agent-<hex>.jsonl`,
`tool-results/result-1.txt`). A conversation with no nested directory emits
`transcript.jsonl` alone.

**Torn-copy detection is a full-content digest, not a size or mtime check.**
`send_conversation` hashes every file that will cross — the top-level
transcript plus, when present, every file under the nested directory, each
hash entry labelled by its member name so a file appearing or disappearing
between the two digests counts as a change too — once immediately before the
producer is spawned and once immediately after `stream_camp` reports
success. A mismatch raises :class:`TranscriptChanged`, naming the
conversation, and is never raised when the transport itself already failed
(:class:`~camp.host.transport.ProducerFailed` and every other non-`Answered`
outcome are returned untouched) — so the abort is always distinguishable from
a transport failure, never a second label for the same one.

**No `--member` on the wire.** Conversations are scoped to the workspace, not
to a member: `workspace_conversations` enumerates workspace-wide and
`subpath` already locates a conversation that started in a member
subdirectory, so the peer resolves the workspace directory from
`--group`/`--slug` alone. `send_conversation` and
`send_workspace_conversations` therefore take no `member` argument at all —
unlike `transfer/history.py` and `transfer/worktree.py`, which are per-member
phases and do carry one.
"""

from __future__ import annotations

import hashlib
import sys
import tarfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Sequence

# `build_conversation_archive_argv` below runs THIS file as a standalone
# script (`python3 <this file> <transcript-path> [nested-dir]`) —
# `stream_camp` needs a real subprocess to read from, mirroring
# `camp.transfer.worktree`'s own standalone-script producer. A script has no
# package context, so the relative imports a few lines down (needed only by
# the sender-side functions, not by the standalone `write_conversation_archive`
# path `__main__` actually uses) would otherwise fail before `__main__` is
# even reached. A no-op when this module is imported normally.
if __package__ in (None, ""):
    _plugin_root = Path(__file__).resolve().parent.parent.parent
    if str(_plugin_root) not in sys.path:
        sys.path.insert(0, str(_plugin_root))
    __package__ = "camp.transfer"

from ..host.config import Host
from ..host.transport import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_SERVER_ALIVE_COUNT_MAX,
    DEFAULT_SERVER_ALIVE_INTERVAL_SECONDS,
    Answered,
    ProducerSpawner,
    StreamSpawner,
    TransportOutcome,
    default_producer_spawn,
    default_stream_spawner,
    stream_camp,
)
from ..launch.recovery import session_candidates
from ..launch.teardown_guard import EnumerationUnavailable

__all__ = [
    "WorkspaceConversation",
    "workspace_conversations",
    "EnumerationUnavailable",
    "UnresolvedConversation",
    "TranscriptUnavailable",
    "TranscriptChanged",
    "build_conversation_archive_argv",
    "write_conversation_archive",
    "send_conversation",
    "send_workspace_conversations",
]

#: The fixed tar member name for a conversation's own top-level transcript —
#: never the session id, so the peer names the file it writes from the wire
#: argument it already validated, not from an archive member it does not
#: otherwise trust.
_TRANSCRIPT_MEMBER = "transcript.jsonl"


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


# ---------------------------------------------------------------------------
# Sender side — one ssh invocation per conversation.
# ---------------------------------------------------------------------------


class UnresolvedConversation(Exception):
    """The enumeration reported this conversation UNRESOLVED — its recorded
    root could not be read at all — so it is refused by name rather than
    silently skipped: a workspace must never half-cross without saying so."""

    def __init__(self, session_id: str) -> None:
        super().__init__(
            f"conversation {session_id} is unresolved (its recorded root "
            "could not be read) and cannot be transferred; refusing rather "
            "than silently leaving it behind"
        )
        self.session_id = session_id


class TranscriptUnavailable(Exception):
    """*locate_transcript* returned ``None`` for a conversation the
    enumeration reported as rooted here. Distinct from
    :class:`UnresolvedConversation`: the enumeration found a readable root,
    but the transcript file it named is gone by the time the sender looked
    for it — a race with retention cleanup, not an unreadable root."""

    def __init__(self, session_id: str) -> None:
        super().__init__(
            f"conversation {session_id}'s transcript could not be located "
            "on disk though its root was resolved; refusing rather than "
            "silently skipping it"
        )
        self.session_id = session_id


class TranscriptChanged(Exception):
    """A full-content digest of *session_id*'s transcript (and any nested
    subtree) taken before the stream started does not match the digest taken
    after it finished — the harness rewrote the file while it was being
    read. Never raised when the transport itself already failed; see the
    module docstring."""

    def __init__(self, session_id: str) -> None:
        super().__init__(
            f"conversation {session_id}'s transcript changed while it was "
            "being streamed; aborting rather than delivering a torn copy"
        )
        self.session_id = session_id


def _conversation_files(
    transcript_path: Path, nested_dir: Path | None
) -> list[tuple[str, Path]]:
    """``(member_name, path)`` for every file *one* conversation contributes
    to the stream — the top-level transcript, always first, then every file
    under *nested_dir* (the conversation's own directory), whatever its
    extension, addressed relative to *nested_dir* itself, in a stable sorted
    order. *nested_dir* is scoped to the conversation's own session-id
    directory, never the shared projects-key directory it sits in, so a
    sibling `memory/` directory at that outer level is never reachable from
    here."""
    files: list[tuple[str, Path]] = [(_TRANSCRIPT_MEMBER, transcript_path)]
    if nested_dir is not None and nested_dir.is_dir():
        for path in sorted(nested_dir.rglob("*")):
            if path.is_file():
                files.append((path.relative_to(nested_dir).as_posix(), path))
    return files


def _digest_conversation(transcript_path: Path, nested_dir: Path | None) -> bytes:
    """A full-content digest of every file :func:`_conversation_files` names
    for this conversation right now — never a size or mtime proxy. Each
    entry is labelled by its member name before its bytes are hashed, so a
    file appearing or disappearing between two calls changes the digest too,
    not only a file whose content changed in place."""
    hasher = hashlib.sha256()
    for name, path in _conversation_files(transcript_path, nested_dir):
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        try:
            hasher.update(path.read_bytes())
        except OSError:
            hasher.update(b"<unreadable>")
        hasher.update(b"\0")
    return hasher.digest()


def write_conversation_archive(
    transcript_path: Path, nested_dir: Path | None, fileobj: BinaryIO
) -> None:
    """Stream a tar of one conversation's files into *fileobj* — the
    top-level transcript as `transcript.jsonl`, then every file under
    *nested_dir*, whatever its extension, named by its path relative to
    *nested_dir*. See the module docstring for the exact member-naming
    contract."""
    with tarfile.open(fileobj=fileobj, mode="w|") as tf:
        for name, path in _conversation_files(transcript_path, nested_dir):
            tf.add(str(path), arcname=name)


def build_conversation_archive_argv(
    transcript_path: Path, nested_dir: Path | None
) -> list[str]:
    """The exact argv for the sender-side producer: this module, run as a
    standalone script, writing :func:`write_conversation_archive`'s stream to
    stdout. Mirrors `transfer/worktree.py`'s `build_archive_argv` — a
    separate process, never a thread, because `stream_camp` requires a real
    `subprocess.Popen` with `stdout=PIPE` it can drain concurrently with
    feeding the remote invocation."""
    argv = [sys.executable, str(Path(__file__).resolve()), str(transcript_path)]
    if nested_dir is not None:
        argv.append(str(nested_dir))
    return argv


def send_conversation(
    host: Host,
    *,
    group: str,
    slug: str,
    session_id: str,
    subpath: PurePosixPath,
    transcript_path: Path,
    nested_dir: Path | None = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    server_alive_interval: float = DEFAULT_SERVER_ALIVE_INTERVAL_SECONDS,
    server_alive_count_max: int = DEFAULT_SERVER_ALIVE_COUNT_MAX,
    extra_ssh_options: Sequence[str] = (),
    spawn: StreamSpawner = default_stream_spawner,
    producer_spawn: ProducerSpawner = default_producer_spawn,
) -> TransportOutcome:
    """Stream one conversation — *transcript_path* plus *nested_dir*, when
    given — into `camp transfer-receive conversations` on *host*.

    The wire carries only *session_id* and *subpath* (rendered as a plain
    string), never a path from this host — see the module docstring.

    Returns whatever `stream_camp` classifies the invocation as — a failed
    producer surfaces as `ProducerFailed`, never as a successful transfer of
    a truncated transcript.

    Raises:
        TranscriptChanged: a full-content digest taken before the stream
            started does not match the digest taken after it finished. Only
            checked when the transport itself reports success
            (`Answered`) — a transport failure is returned untouched, never
            relabelled as a torn copy.
    """
    remote_argv = [
        "transfer-receive",
        "conversations",
        "--group",
        group,
        "--slug",
        slug,
        "--session-id",
        session_id,
        "--subpath",
        str(subpath),
    ]
    before = _digest_conversation(transcript_path, nested_dir)
    producer = producer_spawn(build_conversation_archive_argv(transcript_path, nested_dir))
    outcome = stream_camp(
        host,
        remote_argv,
        producer,
        connect_timeout=connect_timeout,
        server_alive_interval=server_alive_interval,
        server_alive_count_max=server_alive_count_max,
        extra_ssh_options=extra_ssh_options,
        spawn=spawn,
    )
    if isinstance(outcome, Answered):
        after = _digest_conversation(transcript_path, nested_dir)
        if after != before:
            raise TranscriptChanged(session_id)
    return outcome


def send_workspace_conversations(
    host: Host,
    *,
    group: str,
    slug: str,
    workspace: Path,
    conversations: Iterable[WorkspaceConversation],
    locate_transcript: Callable[[str, Path], Path | None],
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    server_alive_interval: float = DEFAULT_SERVER_ALIVE_INTERVAL_SECONDS,
    server_alive_count_max: int = DEFAULT_SERVER_ALIVE_COUNT_MAX,
    extra_ssh_options: Sequence[str] = (),
    spawn: StreamSpawner = default_stream_spawner,
    producer_spawn: ProducerSpawner = default_producer_spawn,
) -> tuple[tuple[str, TransportOutcome], ...]:
    """Stream every row of *conversations* that is rooted here — exactly the
    rows :func:`workspace_conversations` reported, never re-derived: an
    EXCLUDED conversation has no row and is therefore never visited, and a
    row this function is handed is trusted for its `subpath` rather than
    resolving rootedness itself again.

    *locate_transcript* is shaped exactly like
    `Harness.session_transcript_path` — `(session_id, workspace_root) ->
    Path | None` — so this module never learns the projects-directory munge
    rule itself; that stays the harness boundary's alone.

    Raises:
        UnresolvedConversation: a row is UNRESOLVED. Raised before any
            further row is attempted, so a single unresolved conversation
            blocks the whole call rather than the workspace half-crossing.
        TranscriptUnavailable: *locate_transcript* named no file for a row
            the enumeration reported as rooted here.
    """
    root = Path(workspace).resolve()
    results: list[tuple[str, TransportOutcome]] = []
    for conversation in conversations:
        if conversation.unresolved:
            raise UnresolvedConversation(conversation.session_id)

        assert conversation.subpath is not None  # unresolved is False here
        conversation_root = (
            root
            if conversation.subpath == PurePosixPath(".")
            else root.joinpath(*conversation.subpath.parts)
        )
        transcript_path = locate_transcript(conversation.session_id, conversation_root)
        if transcript_path is None:
            raise TranscriptUnavailable(conversation.session_id)

        nested_dir = transcript_path.parent / conversation.session_id
        outcome = send_conversation(
            host,
            group=group,
            slug=slug,
            session_id=conversation.session_id,
            subpath=conversation.subpath,
            transcript_path=transcript_path,
            nested_dir=nested_dir if nested_dir.is_dir() else None,
            connect_timeout=connect_timeout,
            server_alive_interval=server_alive_interval,
            server_alive_count_max=server_alive_count_max,
            extra_ssh_options=extra_ssh_options,
            spawn=spawn,
            producer_spawn=producer_spawn,
        )
        results.append((conversation.session_id, outcome))

    return tuple(results)


def _cli_main(argv: Sequence[str]) -> None:
    transcript_path = Path(argv[0])
    nested_dir = Path(argv[1]) if len(argv) > 1 else None
    write_conversation_archive(transcript_path, nested_dir, sys.stdout.buffer)


if __name__ == "__main__":
    _cli_main(sys.argv[1:])
