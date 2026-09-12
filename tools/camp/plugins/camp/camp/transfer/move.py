"""`camp transfer` (no `--dry-run`) — the sender side that actually moves a
workspace, by driving the four peer-side phases in order.

:func:`move_workspace` is the whole verb: it calls `transfer-receive begin`
over `camp.host.transport.run_camp`, then per member streams that member's
committed history (`camp.transfer.history.send_history`) and working-tree
content (`camp.transfer.worktree.send_worktree`) over
`camp.host.transport.stream_camp`, then calls `transfer-receive finish` over
`run_camp` again. Every path this function reads from is the SENDER's own —
`group["members"]` entries' `repo_root`, the sender's own worktree path
(`camp.provision.reconcile._worktree_path`), the sender's own slug branch
name — continuing `camp.transfer.probe`'s stated posture that no field the
peer returns is ever used to build a path; the only peer-returned field this
module consumes is each member's `basis_commit`, a git sha used only to
negative a bundle, never to build a path.

**Two refusal shapes, not one.** `begin` on the peer already refuses a
workspace present there and owned by a third host, and refuses one present
and owned by the sender unless `--overwrite` is passed (see
`camp.transfer.receive`'s module docstring). The second of those is common
enough — an operator re-running a transfer — that it is raised here as its
own :class:`OverwriteNeeded`, distinguishable from every other phase
failure's :class:`PhaseFailed`, so the CLI layer can give it its own exit
code and remedy text rather than folding it into a generic failure. Both are
raised strictly before the phase's own write — `begin` itself never writes
past either refusal (see `camp.transfer.receive.begin`), and every later
phase is never reached when an earlier one raises, so a caller never needs to
reason about a partial move: whatever raised is the last thing that touched
the peer.

**Phase order and progress.** `on_phase` is called with a short label
immediately BEFORE each phase's network call is made — never after, and
never batched — because the content phases stream a whole worktree or
history and can run for minutes; a caller that renders each label as it
arrives turns a silent multi-minute step into a running commentary. The
caller decides what "immediately" renders as (a printed line, a log
record); this module's only contract is the ordering and the timing
relative to the call it precedes.

**Re-running after a phase failure is safe.** `begin` (see
`camp.transfer.receive.begin`) removes any workspace already present under
this slug on the peer wholesale before seeding a fresh one when `overwrite`
is set, so a retry after `history`, `worktree`, or `finish` fails is a
fresh `begin` call with `--overwrite`, never a resume — nothing from a
half-finished attempt survives into the next one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from ..host.config import Host
from ..host.transport import (
    Answered,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_SERVER_ALIVE_COUNT_MAX,
    DEFAULT_SERVER_ALIVE_INTERVAL_SECONDS,
    ProducerFailed,
    ProducerSpawner,
    RemoteRefusal,
    Runner,
    StreamSpawner,
    TransportOutcome,
    default_producer_spawn,
    default_runner,
    default_stream_spawner,
    run_camp,
)
from .conversations import (
    TranscriptChanged,
    TranscriptUnavailable,
    UnresolvedConversation,
    WorkspaceConversation,
    send_workspace_conversations,
)
from .history import send_history
from .worktree import send_worktree

__all__ = [
    "MoveRefused",
    "OverwriteNeeded",
    "PhaseFailed",
    "ConversationCrossed",
    "MoveResult",
    "move_workspace",
]

#: The substring `camp.transfer.receive.OverwriteRequired`'s message always
#: carries — the sole signal `move_workspace` has for telling that refusal
#: apart from every other reason `begin` can fail over the wire, since the
#: transport layer hands back nothing more structured than exit code and
#: stderr text for a refused remote invocation.
_OVERWRITE_MARKER = "--overwrite"


class MoveRefused(Exception):
    """Base of the two ways `move_workspace` stops before completing."""


class OverwriteNeeded(MoveRefused):
    """`begin` refused: a workspace already exists on the peer under this
    slug, owned by the sender, and `--overwrite` was not passed."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class PhaseFailed(MoveRefused):
    """One phase failed — every phase after it was never attempted.

    Re-running the whole verb (with `--overwrite` if the workspace now
    exists on the peer, which it will once `begin` has run) is always safe;
    see the module docstring.
    """

    def __init__(self, phase: str, detail: str) -> None:
        super().__init__(f"{phase}: {detail}")
        self.phase = phase
        self.detail = detail


@dataclass(frozen=True)
class ConversationCrossed:
    """One conversation `move_workspace` streamed to the peer and the peer
    placed successfully — never a row that was UNRESOLVED or that failed in
    transit, since either of those raises `PhaseFailed` before this is
    built. `subpath` is always resolved (never `None`) for the same reason."""

    session_id: str
    subpath: PurePosixPath


@dataclass(frozen=True)
class MoveResult:
    """What `move_workspace` moved, once every phase has answered."""

    members: tuple[str, ...]
    conversations: tuple[ConversationCrossed, ...] = ()


def _outcome_detail(outcome: TransportOutcome) -> str:
    """A human-legible detail string for a non-`Answered` outcome.

    `RemoteRefusal` carries the remote's own stderr verbatim — the peer's
    `camp transfer-receive: <refusal message>` line — since that message
    already names the specific reason (an unconfigured group, a malformed
    owner, a refused bundle, an escaping archive member, and — see
    `OverwriteNeeded` above — the one case the caller inspects itself).
    Every other outcome kind is transport-level rather than remote-refused,
    so it gets a shape-specific line instead.
    """
    if isinstance(outcome, RemoteRefusal):
        return outcome.stderr.strip()
    if isinstance(outcome, ProducerFailed):
        return f"the local producer exited {outcome.exit_code} before the stream completed"
    kind = type(outcome).__name__
    reason = getattr(outcome, "reason", None)
    if reason is not None:
        return f"{kind}: {reason}"
    execution_timeout = getattr(outcome, "execution_timeout", None)
    if execution_timeout is not None:
        return f"{kind}: no response within {execution_timeout}s"
    return kind


def _run_camp_phase(
    host: Host,
    remote_argv: list[str],
    *,
    run: Runner,
    connect_timeout: float,
    execution_timeout: float,
) -> Answered:
    outcome = run_camp(
        host,
        remote_argv,
        connect_timeout=connect_timeout,
        execution_timeout=execution_timeout,
        runner=run,
    )
    if isinstance(outcome, Answered):
        return outcome
    if isinstance(outcome, RemoteRefusal) and _OVERWRITE_MARKER in outcome.stderr:
        raise OverwriteNeeded(outcome.stderr.strip())
    raise PhaseFailed(remote_argv[1] if len(remote_argv) > 1 else remote_argv[0], _outcome_detail(outcome))


def move_workspace(
    *,
    host: Host,
    group: dict[str, Any],
    group_name: str,
    slug: str,
    sender_name: str,
    overwrite: bool,
    on_phase: Callable[[str], None] = lambda phase: None,
    run: Runner = default_runner,
    stream_spawn: StreamSpawner = default_stream_spawner,
    history_producer_spawn: ProducerSpawner = default_producer_spawn,
    worktree_producer_spawn: ProducerSpawner = default_producer_spawn,
    conversation_producer_spawn: ProducerSpawner = default_producer_spawn,
    conversations: Iterable[WorkspaceConversation] = (),
    locate_transcript: Callable[[str, Path], Path | None] | None = None,
    env: dict[str, str] | None = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    execution_timeout: float = 60.0,
    server_alive_interval: float = DEFAULT_SERVER_ALIVE_INTERVAL_SECONDS,
    server_alive_count_max: int = DEFAULT_SERVER_ALIVE_COUNT_MAX,
) -> MoveResult:
    """Move *slug*'s content — every declared member's committed history and
    working tree, minus each member's declared `excluded` set — to *host*.

    Calls `on_phase` with a short label before `begin`, before each member's
    `history` and `worktree` phase, before `conversations` (once, only when
    *conversations* is non-empty — a workspace with nothing rooted in it
    never announces a phase with nothing to do), and before `finish` — see
    the module docstring for the ordering and timing contract.

    *conversations* is the already-enumerated pool the caller showed the
    operator at preflight — never re-enumerated here, so the move can never
    disagree with what was previewed. *locate_transcript* is required when
    *conversations* is non-empty and is passed straight through to
    `camp.transfer.conversations.send_workspace_conversations`.

    Raises:
        OverwriteNeeded: `begin` refused because the workspace already
            exists on the peer, owned by the sender, and *overwrite* is
            False. Nothing crossed.
        PhaseFailed: any other phase refused or the transport failed. A
            conversation that was UNRESOLVED, whose transcript could not be
            located, whose content changed mid-stream, or whose transport
            failed is reported as phase `"conversations"` — the same
            re-runnable shape every other phase failure uses. Nothing after
            the failing phase was attempted.
    """
    from ..provision.reconcile import _branch_name, _worktree_path

    on_phase("begin")
    begin_argv = [
        "transfer-receive",
        "begin",
        "--group",
        group_name,
        "--slug",
        slug,
        "--owner",
        sender_name,
    ]
    if overwrite:
        begin_argv.append("--overwrite")
    begin_answer = _run_camp_phase(
        host, begin_argv, run=run, connect_timeout=connect_timeout, execution_timeout=execution_timeout
    )

    import json

    begin_payload = json.loads(begin_answer.stdout)
    basis_by_member = {m["name"]: m["basis_commit"] for m in begin_payload["members"]}

    branch_pattern: str = group.get("branch_pattern", "worktree-{slug}")
    branch = _branch_name(slug, branch_pattern)
    members: list[dict[str, Any]] = group["members"]

    for member in members:
        name = member["name"]
        on_phase(f"history: {name}")
        repo_root = Path(member["repo_root"])
        outcome = send_history(
            host,
            group=group_name,
            slug=slug,
            member=name,
            repo_root=repo_root,
            ref=branch,
            basis_commit=basis_by_member.get(name),
            connect_timeout=connect_timeout,
            server_alive_interval=server_alive_interval,
            server_alive_count_max=server_alive_count_max,
            spawn=stream_spawn,
            producer_spawn=history_producer_spawn,
        )
        if not isinstance(outcome, Answered):
            raise PhaseFailed(f"history ({name})", _outcome_detail(outcome))

    for member in members:
        name = member["name"]
        on_phase(f"worktree: {name}")
        wt_path = _worktree_path(group_name, slug, name, env=env)
        excluded = member.get("excluded") or ()
        outcome = send_worktree(
            host,
            group=group_name,
            slug=slug,
            member=name,
            worktree=wt_path,
            excluded=excluded,
            connect_timeout=connect_timeout,
            server_alive_interval=server_alive_interval,
            server_alive_count_max=server_alive_count_max,
            spawn=stream_spawn,
            producer_spawn=worktree_producer_spawn,
        )
        if not isinstance(outcome, Answered):
            raise PhaseFailed(f"worktree ({name})", _outcome_detail(outcome))

    conversations = tuple(conversations)
    crossed: list[ConversationCrossed] = []
    if conversations:
        on_phase("conversations")
        from ..group.manifest import workspace_dir

        assert locate_transcript is not None  # required whenever conversations is non-empty
        try:
            outcomes = send_workspace_conversations(
                host,
                group=group_name,
                slug=slug,
                workspace=workspace_dir(group_name, slug, env=env),
                conversations=conversations,
                locate_transcript=locate_transcript,
                connect_timeout=connect_timeout,
                server_alive_interval=server_alive_interval,
                server_alive_count_max=server_alive_count_max,
                spawn=stream_spawn,
                producer_spawn=conversation_producer_spawn,
            )
        except (UnresolvedConversation, TranscriptUnavailable, TranscriptChanged) as e:
            raise PhaseFailed("conversations", str(e)) from e

        by_id = {c.session_id: c for c in conversations}
        for session_id, outcome in outcomes:
            if not isinstance(outcome, Answered):
                raise PhaseFailed(f"conversations ({session_id})", _outcome_detail(outcome))
            subpath = by_id[session_id].subpath
            assert subpath is not None  # unresolved rows never reach here
            crossed.append(ConversationCrossed(session_id=session_id, subpath=subpath))

    on_phase("finish")
    finish_argv = ["transfer-receive", "finish", "--group", group_name, "--slug", slug]
    _run_camp_phase(
        host, finish_argv, run=run, connect_timeout=connect_timeout, execution_timeout=execution_timeout
    )

    return MoveResult(members=tuple(m["name"] for m in members), conversations=tuple(crossed))
