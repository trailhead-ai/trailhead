"""`camp transfer` (no `--dry-run`) — the sender side that actually moves a
workspace, by driving the peer-side phases in order.

:func:`move_workspace` is the whole verb: it calls `transfer-receive begin`
over `camp.host.transport.run_camp`, then per member streams that member's
committed history (`camp.transfer.history.send_history`) and working-tree
content (`camp.transfer.worktree.send_worktree`) over
`camp.host.transport.stream_camp`, then (whenever there is anything rooted in
the workspace) streams each conversation, then calls `transfer-receive
claim` over `run_camp` — the peer's single commit point, where it writes
itself as the workspace's owner and answers with the name it wrote (see
`camp.transfer.receive`'s `claim` section) — and finally calls
`transfer-receive finish` over `run_camp` again. `claim` runs strictly
before `finish` because `finish` triggers a manifest rebuild that only
carries an owner forward, never sets one; a claim not yet durable on disk
when that rebuild runs would not be preserved.

Once `move_workspace` returns (every phase, including `finish`, has
answered), the CLI layer (`camp.cli.transfer._cmd_transfer_group_cli`) drives
two more, purely local steps this module does not perform itself, in a fixed
order: `camp.transfer.release.release_conversations` moves the sender's own
copies of `MoveResult.conversations` out of the harness's transcript store
into a camp-owned archive (appending a durable marker per conversation moved),
so the sender stops offering and resuming them; only once that has returned
does `camp.transfer.release.flip_sender_ownership` write THIS host's own
manifest to name `MoveResult.claimed_owner` as owner — the sender's own last
write of the whole verb. Neither step runs when `move_workspace` raises — a
`PhaseFailed` after `claim` but before `finish` answers is indistinguishable,
from this module's own exceptions, from one before `claim`, so the CLI treats
every raised phase failure as "ownership has not been confirmed to have
moved" and releases and flips nothing. Every path this function reads from is
the SENDER's own —
`group["members"]` entries' `repo_root`, the sender's own worktree path
(`camp.provision.reconcile._worktree_path`), the sender's own slug branch
name — continuing `camp.transfer.probe`'s stated posture that no field the
peer returns is ever used to build a path; the two peer-returned fields this
module consumes are each member's `basis_commit` (a git sha used only to
negative a bundle, never to build a path) and `claim`'s `owner` (a name
stamped into the caller's own `MoveResult`, never used to build a path
either).

**Three refusal shapes, not one.** `begin` on the peer already refuses a
workspace present there whose own record does not name the sender as owner
— a third host's own workspace, or one that never recorded an owner at all
— regardless of `--overwrite`, and refuses one owned by the sender unless
`--overwrite` is passed (see `camp.transfer.receive`'s module docstring).
The overwrite-required case is common enough — an operator re-running a
transfer — that it is raised here as its own :class:`OverwriteNeeded`; the
unattributed case is raised as :class:`UnattributedCollision`. Both are
distinguishable from every other phase failure's :class:`PhaseFailed`, so
the CLI layer can give each its own exit code and remedy text rather than
folding it into a generic failure. All three are raised strictly before the
phase's own write — `begin` itself never writes past any of them (see
`camp.transfer.receive.begin`), and every later phase is never reached when
an earlier one raises, so a caller never needs to reason about a partial
move: whatever raised is the last thing that touched the peer.

**Phase order and progress.** `on_phase` is called with a short label
immediately BEFORE each phase's network call is made — never after, and
never batched — because the content phases stream a whole worktree or
history and can run for minutes; a caller that renders each label as it
arrives turns a silent multi-minute step into a running commentary. The
caller decides what "immediately" renders as (a printed line, a log
record); this module's only contract is the ordering and the timing
relative to the call it precedes.

**Re-running after a phase failure is safe, UNLESS `claim` already
answered.** `begin` (see `camp.transfer.receive.begin`) removes any
workspace already present under this slug on the peer wholesale before
seeding a fresh one when `overwrite` is set, so a retry after `begin`,
`history`, `worktree`, or `conversations` fails is a fresh `begin` call with
`--overwrite`, never a resume — nothing from a half-finished attempt
survives into the next one. `finish` is the one phase this does not hold
for: it runs strictly after `claim`, the commit point, so a `finish` failure
raises `PhaseFailed` with `claimed_owner` populated — see that class's own
docstring — and a re-run would find the peer already owns the workspace and
refuse `begin`'s own opening check.
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
    StoppedResponding,
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
    "UnattributedCollision",
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

#: The substring `camp.transfer.receive.UnattributedWorkspace`'s message
#: always carries — checked BEFORE `_OVERWRITE_MARKER` below, since that
#: message also mentions `--overwrite` (to say the refusal is not lifted by
#: it) and would otherwise be misread as `OverwriteNeeded`.
_UNATTRIBUTED_MARKER = "was never handed this workspace"


class MoveRefused(Exception):
    """Base of the two ways `move_workspace` stops before completing."""


class OverwriteNeeded(MoveRefused):
    """`begin` refused: a workspace already exists on the peer under this
    slug, owned by the sender, and `--overwrite` was not passed."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class UnattributedCollision(MoveRefused):
    """`begin` refused: the peer already holds something of this slug's name
    whose own record does not attribute it to the sender — refused
    regardless of `--overwrite`.

    `kind` is the stable discriminant a caller inspects instead of parsing
    `detail`: `"workspace"` (this module's only value today) means a
    pre-existing workspace record naming a different owner, or none at all;
    `"branch"` is reserved for a same-named git branch collision with no
    workspace record at all.
    """

    def __init__(self, detail: str, *, kind: str) -> None:
        super().__init__(detail)
        self.detail = detail
        self.kind = kind


class PhaseFailed(MoveRefused):
    """One phase failed — every phase after it was never attempted.

    Re-running the whole verb (with `--overwrite` if the workspace now
    exists on the peer, which it will once `begin` has run) is safe UNLESS
    `claimed_owner` is populated OR `indeterminate` is `True`. `claimed_owner`
    populated means `claim` is known to have already answered — either
    `finish` failed after it, or `claim` itself failed in a way this module
    could confirm actually landed on the peer (a network-layer timeout after
    the connection completed, or an unparseable answer body on an
    otherwise-successful exit — both re-probe the peer via `camp
    transfer-probe` to find out rather than guessing; see `move_workspace`'s
    own `claim` handling) — the peer already owns the workspace and would
    refuse a re-run's opening `begin`, since `begin` only tears down a prior
    attempt owned by the sender. `indeterminate` is `True` when `claim`'s own
    outcome AND the re-probe used to resolve it both failed to establish
    whether the claim landed — neither the pre-commit nor the post-commit
    remedy is safe to assume here, and the operator must check the peer by
    hand before doing anything. `claimed_owner`/`conversations`/
    `indeterminate` are `None`/empty/`False` for every phase failure known
    NOT to have landed (`begin`, `history`, `worktree`, `conversations`, or a
    `claim` explicitly refused or never reaching the peer at all);
    `claimed_owner`/`conversations` carry the exact values `MoveResult` would
    have carried on success — the peer's own declared name and the pool of
    conversations that already crossed — whenever a landed `claim` is
    confirmed, so the caller can still archive what already crossed without
    flipping this host's own ownership record, which stays correctly stale
    until the two hosts' manifests are reconciled by hand.
    """

    def __init__(
        self,
        phase: str,
        detail: str,
        *,
        claimed_owner: str | None = None,
        conversations: tuple[ConversationCrossed, ...] = (),
        indeterminate: bool = False,
    ) -> None:
        super().__init__(f"{phase}: {detail}")
        self.phase = phase
        self.detail = detail
        self.claimed_owner = claimed_owner
        self.conversations = conversations
        self.indeterminate = indeterminate


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
    """What `move_workspace` moved, once every phase has answered.

    `claimed_owner` is the exact name the peer's `claim` phase answered —
    its own declared host name, never the sender's local alias for it (see
    `camp.transfer.receive.claim`). Always populated once `move_workspace`
    returns; `None` only for a `MoveResult` a test constructs directly
    without driving `claim`.
    """

    members: tuple[str, ...]
    conversations: tuple[ConversationCrossed, ...] = ()
    claimed_owner: str | None = None


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
    promote_overwrite: bool = False,
    promote_unattributed: bool = False,
) -> Answered:
    """Run one `transfer-receive` phase and classify the outcome.

    *promote_overwrite* scopes the `--overwrite` substring promotion to the
    one phase it is meaningful for: `begin`, the only phase `--overwrite`
    governs. Every other phase this function drives runs after `claim` has
    already answered, so a refusal whose stderr happens to mention the flag
    for any incidental reason must never be promoted to `OverwriteNeeded` —
    that exception is caught nowhere past `claim` (see `move_workspace`'s
    own `except PhaseFailed` around its `finish` call), so promoting it here
    would escape as an uncaught "nothing crossed" refusal after ownership
    had, in fact, already moved.

    *promote_unattributed* scopes the `UnattributedCollision` promotion the
    same way, to the same one phase — `begin` is the only phase that can
    raise `camp.transfer.receive.UnattributedWorkspace`. Checked BEFORE the
    overwrite promotion: that refusal's own message also contains
    `--overwrite` (to say the refusal is not lifted by it), so checking
    overwrite first would misclassify it.
    """
    outcome = run_camp(
        host,
        remote_argv,
        connect_timeout=connect_timeout,
        execution_timeout=execution_timeout,
        runner=run,
    )
    if isinstance(outcome, Answered):
        return outcome
    if promote_unattributed and isinstance(outcome, RemoteRefusal) and _UNATTRIBUTED_MARKER in outcome.stderr:
        raise UnattributedCollision(outcome.stderr.strip(), kind="workspace")
    if promote_overwrite and isinstance(outcome, RemoteRefusal) and _OVERWRITE_MARKER in outcome.stderr:
        raise OverwriteNeeded(outcome.stderr.strip())
    raise PhaseFailed(remote_argv[1] if len(remote_argv) > 1 else remote_argv[0], _outcome_detail(outcome))


def _reprobe_claim(
    host: Host,
    *,
    group_name: str,
    slug: str,
    sender_name: str,
    run: Runner,
    connect_timeout: float,
    execution_timeout: float,
    remote_confirmed_exited: bool,
) -> tuple[bool | None, str | None]:
    """Ask *host* itself, via `camp.transfer.probe.probe_peer`, whether a
    `claim` this module could not read directly actually landed there.

    Returns `(True, owner)` when the peer's own probe answer names ITSELF as
    the workspace's owner — `claim` landed, and *owner* is the exact name to
    carry forward as `PhaseFailed.claimed_owner`. Returns `(None, None)`
    when the probe itself could not establish either — unreachable, refused,
    a malformed wire response, or a declared-name collision — so the caller
    must not guess and should raise with `indeterminate=True` instead of
    picking one of the other two.

    A probe answering cleanly with the peer NOT (yet) considering itself the
    owner is only conclusive — `(False, None)`, "claim did not land" — when
    *remote_confirmed_exited* is `True`: the original outcome this call is
    resolving was itself proof the remote `claim` invocation had already run
    to completion (an `Answered` exit 0 with an unusable body, or a
    `RemoteRefusal`'s own nonzero exit — either way the ssh session
    completed). When *remote_confirmed_exited* is `False` — a
    `StoppedResponding` timeout, where only the LOCAL connection is known to
    have ended — a negative reading is not proof of anything: the remote
    `claim` may still be running, blocked on the workspace lock its own
    provisioner holds for minutes, and land moments after this probe
    returns. That case is reported `(None, None)` too, so the caller raises
    `indeterminate=True` rather than the pre-commit "safe to retry" shape.
    """
    from .probe import ProbeAnswer, probe_peer

    probe_result = probe_peer(
        host,
        group=group_name,
        slug=slug,
        self_name=sender_name,
        connect_timeout=connect_timeout,
        execution_timeout=execution_timeout,
        runner=run,
    )
    if not isinstance(probe_result, ProbeAnswer) or probe_result.self_name is None:
        return None, None
    if probe_result.workspace_owner == probe_result.self_name:
        return True, probe_result.self_name
    if not remote_confirmed_exited:
        return None, None
    return False, None


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
    never announces a phase with nothing to do), before `claim`, and before
    `finish` — see the module docstring for the ordering and timing
    contract.

    This function deliberately does not observe whatever provisioning the
    peer's `finish` phase spawns — it does not block on it and does not poll
    it. By the time `claim` has answered, ownership is already settled and
    every byte that crosses has already crossed, so a provisioning failure
    on the peer afterward is a pending bring-up, not data loss, and the peer
    already owns detecting and remedying it.

    *conversations* is the already-enumerated pool the caller showed the
    operator at preflight — never re-enumerated here, so the move can never
    disagree with what was previewed. *locate_transcript* is required when
    *conversations* is non-empty and is passed straight through to
    `camp.transfer.conversations.send_workspace_conversations`.

    Raises:
        OverwriteNeeded: `begin` refused because the workspace already
            exists on the peer, owned by the sender, and *overwrite* is
            False. Nothing crossed.
        UnattributedCollision: `begin` refused because the workspace already
            exists on the peer and its own record does not attribute it to
            the sender — a third host's own workspace, or one that never
            recorded an owner at all. Raised regardless of *overwrite*.
            Nothing crossed.
        PhaseFailed: any other phase refused or the transport failed. A
            conversation that was UNRESOLVED, whose transcript could not be
            located, whose content changed mid-stream, or whose transport
            failed is reported as phase `"conversations"` — the same
            re-runnable shape every other phase failure uses. A peer that
            does not recognize the `claim` subcommand at all (an older camp
            build) refuses it the same way any unrecognized phase refuses,
            surfacing here as `PhaseFailed("claim", ...)` with no
            `claimed_owner` — ownership never moved, since the peer's own
            explicit refusal means `claim` never ran there at all. A `claim`
            outcome this module cannot read directly — a network-layer
            timeout after the connection completed, or exit 0 with an
            answer body that will not parse — is never assumed either way:
            it is resolved by re-probing the peer (`camp.transfer.probe`)
            before raising, so the resulting `PhaseFailed` carries
            `claimed_owner` (and `conversations`) when the probe confirms
            the claim landed, carries neither when the probe confirms it did
            not, and carries `indeterminate=True` when the probe itself
            could not tell — see `PhaseFailed`'s own docstring for the full
            three-way split. Nothing after the failing phase was attempted
            in any of these cases. A `finish` failure is the other case that
            carries `claimed_owner`: it runs after `claim` has already
            answered, so the raised `PhaseFailed` carries `claimed_owner`
            and `conversations` populated exactly as a successful
            `MoveResult` would — see `PhaseFailed`'s own docstring for what
            that changes for the caller.
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
        host,
        begin_argv,
        run=run,
        connect_timeout=connect_timeout,
        execution_timeout=execution_timeout,
        promote_overwrite=True,
        promote_unattributed=True,
    )

    import json

    try:
        begin_payload = json.loads(begin_answer.stdout)
        basis_by_member = {m["name"]: m["basis_commit"] for m in begin_payload["members"]}
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        # `begin` is always pre-commit — an exit-0 answer with an unusable
        # body (shell-profile noise on the remote is the classic cause) is
        # no different from any other `begin` failure: nothing has written
        # anything anywhere yet, so this is the ordinary, safe-to-retry
        # shape, never `claimed_owner`.
        raise PhaseFailed("begin", f"peer answered but the response body was not usable: {e}") from e

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

    on_phase("claim")
    claim_argv = [
        "transfer-receive",
        "claim",
        "--group",
        group_name,
        "--slug",
        slug,
        "--owner",
        sender_name,
    ]
    claim_outcome = run_camp(
        host, claim_argv, connect_timeout=connect_timeout, execution_timeout=execution_timeout, runner=run
    )

    if isinstance(claim_outcome, Answered):
        try:
            claim_payload = json.loads(claim_outcome.stdout)
            claimed_owner = claim_payload["owner"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            # Exit 0 means the remote `claim` phase ran to completion and
            # already wrote itself as owner (see
            # `camp.transfer.receive.claim`) before producing this unusable
            # body — this is a commit hidden behind a parse failure, not an
            # ordinary pre-commit one. Resolve it exactly like a claim that
            # timed out below: re-probe rather than guess.
            landed, owner = _reprobe_claim(
                host,
                group_name=group_name,
                slug=slug,
                sender_name=sender_name,
                run=run,
                connect_timeout=connect_timeout,
                execution_timeout=execution_timeout,
                remote_confirmed_exited=True,
            )
            detail = f"peer answered but the response body was not usable: {e}"
            if landed:
                raise PhaseFailed(
                    "claim", detail, claimed_owner=owner, conversations=tuple(crossed)
                ) from e
            if landed is False:
                raise PhaseFailed("claim", detail) from e
            raise PhaseFailed("claim", detail, indeterminate=True) from e
    elif isinstance(claim_outcome, StoppedResponding):
        # The connection completed, so the peer may have run `claim` to
        # completion before this host lost the response — but the LOCAL
        # timeout proves nothing about whether the remote invocation itself
        # ever terminated, so a negative reprobe reading is not conclusive
        # either (`remote_confirmed_exited=False`); see `_reprobe_claim`.
        landed, owner = _reprobe_claim(
            host,
            group_name=group_name,
            slug=slug,
            sender_name=sender_name,
            run=run,
            connect_timeout=connect_timeout,
            execution_timeout=execution_timeout,
            remote_confirmed_exited=False,
        )
        detail = _outcome_detail(claim_outcome)
        if landed:
            raise PhaseFailed("claim", detail, claimed_owner=owner, conversations=tuple(crossed))
        if landed is False:
            raise PhaseFailed("claim", detail)
        raise PhaseFailed("claim", detail, indeterminate=True)
    elif isinstance(claim_outcome, RemoteRefusal):
        # A nonzero remote exit is not proof `claim` never ran: it is
        # equally the shape a crash AFTER the manifest write would produce
        # (see `camp.transfer.receive.claim` and its own marker-append
        # note). The remote process is confirmed to have fully exited here,
        # though — unlike `StoppedResponding` — so a negative reprobe
        # reading IS conclusive (`remote_confirmed_exited=True`).
        landed, owner = _reprobe_claim(
            host,
            group_name=group_name,
            slug=slug,
            sender_name=sender_name,
            run=run,
            connect_timeout=connect_timeout,
            execution_timeout=execution_timeout,
            remote_confirmed_exited=True,
        )
        detail = _outcome_detail(claim_outcome)
        if landed:
            raise PhaseFailed("claim", detail, claimed_owner=owner, conversations=tuple(crossed))
        if landed is False:
            raise PhaseFailed("claim", detail)
        raise PhaseFailed("claim", detail, indeterminate=True)
    else:
        # Every remaining outcome — a transport failure before the remote
        # ever ran (`Unreachable`, `IdentityUnknown`/`IdentityChanged`,
        # `CampNotResolvable`, `CredentialsRefused`) — means the connection
        # itself never completed, so `claim` never ran on the peer at all:
        # the ordinary, safe-to-retry shape, with no need to re-probe (a
        # probe would only fail to connect the same way).
        raise PhaseFailed("claim", _outcome_detail(claim_outcome))

    on_phase("finish")
    finish_argv = ["transfer-receive", "finish", "--group", group_name, "--slug", slug]
    try:
        _run_camp_phase(
            host, finish_argv, run=run, connect_timeout=connect_timeout, execution_timeout=execution_timeout
        )
    except PhaseFailed as e:
        # `claim` already answered by this point — ownership has already
        # moved to the peer, so this failure is not the re-runnable kind
        # every earlier phase's `PhaseFailed` is. Carry what `MoveResult`
        # would have carried on success so the caller can still archive what
        # already crossed without treating the handover as unconfirmed.
        raise PhaseFailed(
            e.phase, e.detail, claimed_owner=claimed_owner, conversations=tuple(crossed)
        ) from e

    return MoveResult(
        members=tuple(m["name"] for m in members),
        conversations=tuple(crossed),
        claimed_owner=claimed_owner,
    )
