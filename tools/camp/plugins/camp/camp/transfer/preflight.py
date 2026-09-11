"""The transfer preflight composition — checks as pure data.

This module is PURE: every function here maps data to data. It reads nothing
from disk, resolves no path, starts no process, and prints nothing.
Rendering, exit codes, and refusal wording belong to camp's CLI layer, which
performs every local read (the manifest, the group config, the excluded
declarations, the conversation pool) and the one peer read
(:func:`camp.transfer.probe.probe_peer`) and hands the *results* of those
reads to :func:`compose_preflight` here. Mirrors the purity boundary
`camp.launch.recovery` already holds and states about itself.
`tools/camp/tests/test_transfer_preflight.py` guards it three ways, each by
running a composition and observing what it did: a byte-identical snapshot of
the whole camp state directory across the call (no write), the captured
stdout and stderr (no rendering), and every process-spawning entry point in
`subprocess` made to raise (no process).

THE CHECKS, IN ORDER, AND WHY ELEVEN. Each of the eleven checks below reports
independently — one failing check never suppresses the rest, because the
operator reaches for this while tired and moving between machines, and a
preflight that stops at the first problem turns one round trip into several.
Every check produces exactly one :class:`Check`, in this fixed order:

1. this host has declared a name
2. the workspace exists here (the MANIFEST-PRESENCE predicate — camp has
   three incompatible "does this workspace exist?" predicates, and this one
   is the same one `camp list` and the probe's own answer use)
3. this host owns it, or it was never recorded
4. the named peer is declared
5. the peer answers
6. the peer's declared name differs from this host's
7. the peer has the group configured with existing member repo roots
8. the peer's harness account binding matches this end's
9. the slug is free on the peer, or present there and owned by this host
10. every member declares an excluded set
11. the conversations rooted here are enumerated

THREE STATES, NEVER TWO. Every :class:`Check` carries a :class:`CheckStatus`
of PASSED, FAILED, or INDETERMINATE. Checks 5-9 depend on the peer's answer,
which arrives (via the caller, from `camp.transfer.probe.probe_peer`) as one
of the already-classified `camp.host.transport.TransportOutcome` subtypes, a
:class:`~camp.transfer.probe.ProbeAnswer`, a
:class:`~camp.transfer.probe.ProbeRefused`, or a
:class:`~camp.transfer.probe.SelfNameCollision` — a closed set this module
consumes verbatim and never re-derives. A `TransportOutcome` means the peer
never actually answered, so every check that needs its payload goes
INDETERMINATE, carrying that *same* outcome object on
:attr:`Check.transport_outcome` — a changed host key
(:class:`~camp.host.transport.IdentityChanged`) and an ordinary connection
timeout (:class:`~camp.host.transport.Unreachable`) produce different
`Check.detail` text and a different `transport_outcome`, so they never read
as the same grey row. A malformed wire response
(:class:`~camp.transfer.probe.ProbeRefused`) or a self-name collision
(:class:`~camp.transfer.probe.SelfNameCollision`) is not a transport failure
— the peer did answer — so those checks go FAILED, not INDETERMINATE;
`transport_outcome` stays `None` on a FAILED check, since none produced it.
An INDETERMINATE check can never resolve to a clean verdict, and no check
here ever reports PASSED for something it could not actually observe.

THE VERDICT. :attr:`PreflightResult.verdict` is
:attr:`Verdict.WOULD_TRANSFER` only when every check is PASSED; any FAILED or
INDETERMINATE check yields :attr:`Verdict.NOT_CLEAN`. A live conversation
does not by itself make the verdict unclean — nothing moves on this path, so
a live conversation is only ever reported, never penalized.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..host.transport import TransportOutcome
from .conversations import WorkspaceConversation
from .probe import ProbeAnswer, ProbeRefused, SelfNameCollision

__all__ = [
    "CheckStatus",
    "Verdict",
    "Check",
    "MemberDeclaration",
    "PreflightResult",
    "compose_preflight",
]


class CheckStatus(Enum):
    """The three states a single preflight check may report. Never two."""

    PASSED = "passed"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class Verdict(Enum):
    """The one resolved outcome of the whole composition."""

    WOULD_TRANSFER = "would transfer"
    NOT_CLEAN = "not clean"


@dataclass(frozen=True)
class Check:
    """One independently reported preflight check.

    ``transport_outcome`` is set only on an INDETERMINATE check produced by a
    peer that never actually answered — it carries the very
    `camp.host.transport.TransportOutcome` the caller's `probe_peer` call
    returned, unchanged, so a changed host key and an ordinary timeout stay
    distinguishable in the data rather than collapsing into the same grey
    row. It is always `None` on a PASSED or FAILED check.
    """

    name: str
    status: CheckStatus
    detail: str
    transport_outcome: TransportOutcome | None = None


@dataclass(frozen=True)
class MemberDeclaration:
    """One member's declared regenerable state, as this host's group config holds it.

    ``excluded`` mirrors `camp.group.config`'s own three-valued handoff: `None`
    means the member never declared an `excluded` key at all, `()` means it
    declared the key with no entries, and a non-empty tuple carries the
    declared paths in order.
    """

    name: str
    excluded: tuple[str, ...] | None


@dataclass(frozen=True)
class PreflightResult:
    """The whole composition: every check, the resolved verdict, and what a
    "would transfer" verdict would actually move or regenerate."""

    checks: tuple[Check, ...]
    verdict: Verdict
    conversations: tuple[WorkspaceConversation, ...]
    regenerated: tuple[MemberDeclaration, ...]


def _transport_detail(outcome: TransportOutcome) -> str:
    """A human-legible, per-outcome-type detail string.

    One branch per `TransportOutcome` subtype so each outcome's `Check.detail`
    text is lexically distinct from every other's — the same distinguishing
    duty `transport_outcome` carries structurally, restated in prose for a
    reader who only sees the rendered check.
    """
    kind = type(outcome).__name__
    if kind == "Unreachable":
        return f"peer is unreachable — {outcome.reason}"
    if kind == "StoppedResponding":
        return f"peer stopped responding within {outcome.execution_timeout}s"
    if kind == "IdentityUnknown":
        return "peer host identity is unknown (no pinned key)"
    if kind == "IdentityChanged":
        return "peer host identity changed since it was last pinned"
    if kind == "CredentialsRefused":
        return "peer refused every credential offered"
    if kind == "CampNotResolvable":
        return "camp is not resolvable on the peer"
    if kind == "RemoteRefusal":
        return f"peer camp refused (exit {outcome.exit_code}) — {outcome.stderr.strip()}"
    return f"peer transport outcome: {kind}"  # pragma: no cover - exhaustive above


def _peer_payload_unavailable(
    name: str,
    probe_result: TransportOutcome | ProbeAnswer | ProbeRefused | SelfNameCollision | None,
) -> Check | None:
    """The check named *name* answered from the peer's non-answer alone.

    Checks 6-9 each need a field of the peer's parsed
    :class:`~camp.transfer.probe.ProbeAnswer`, and every one of them owes the
    same answer when there is no such answer to read: INDETERMINATE carrying
    the `TransportOutcome` that stopped the peer from answering, FAILED for a
    malformed payload or a self-name collision (the peer *did* answer, so
    neither is a transport failure), and FAILED when the peer was never
    probed at all. `None` means the peer returned a parsed answer, so the
    caller evaluates its own predicate against it.

    The collision detail here is the generic one, for a check that cannot
    evaluate its own question while the two hosts are indistinguishable. The
    check whose question *is* the collision states it specifically, at its
    own site, before consulting this.
    """
    if isinstance(probe_result, TransportOutcome):
        return Check(
            name,
            CheckStatus.INDETERMINATE,
            f"indeterminate — {_transport_detail(probe_result)}",
            transport_outcome=probe_result,
        )
    if isinstance(probe_result, ProbeRefused):
        return Check(
            name,
            CheckStatus.FAILED,
            f"cannot evaluate — the peer's response was malformed: {probe_result.reason}",
        )
    if isinstance(probe_result, SelfNameCollision):
        return Check(
            name,
            CheckStatus.FAILED,
            "cannot evaluate — the peer declares the same name as this host",
        )
    if not isinstance(probe_result, ProbeAnswer):
        return Check(name, CheckStatus.FAILED, "the peer was never probed")
    return None


def compose_preflight(
    *,
    self_name: str | None,
    self_account: str | None,
    workspace_manifest_exists: bool,
    owner: str | None,
    peer_name: str,
    peer_declared: bool,
    probe_result: TransportOutcome | ProbeAnswer | ProbeRefused | SelfNameCollision | None,
    members: tuple[MemberDeclaration, ...],
    slug: str,
    conversations: tuple[WorkspaceConversation, ...] | None,
) -> PreflightResult:
    """Compose the eleven checks and the resolved verdict, as pure data.

    Every argument is already the result of a read the caller performed —
    this function does no I/O of its own. See the module docstring for the
    fixed check order and the three-state rule.
    """
    checks: list[Check] = []

    # 1. this host has declared a name
    if self_name is not None:
        checks.append(
            Check(
                "this host has declared a name",
                CheckStatus.PASSED,
                f"this host declares itself {self_name!r}",
            )
        )
    else:
        checks.append(
            Check(
                "this host has declared a name",
                CheckStatus.FAILED,
                "this host has no declared name",
            )
        )

    # 2. the workspace exists here (manifest-presence predicate)
    if workspace_manifest_exists:
        checks.append(
            Check(
                "the workspace exists here",
                CheckStatus.PASSED,
                f"a workspace manifest for {slug!r} is present (manifest presence)",
            )
        )
    else:
        checks.append(
            Check(
                "the workspace exists here",
                CheckStatus.FAILED,
                f"no workspace manifest is recorded for {slug!r} (manifest presence)",
            )
        )

    # 3. this host owns it, or it was never recorded
    if owner is None:
        checks.append(
            Check(
                "this host owns it, or it was never recorded",
                CheckStatus.PASSED,
                "ownership was never recorded",
            )
        )
    elif owner == self_name:
        checks.append(
            Check(
                "this host owns it, or it was never recorded",
                CheckStatus.PASSED,
                f"owned by this host ({self_name!r})",
            )
        )
    else:
        checks.append(
            Check(
                "this host owns it, or it was never recorded",
                CheckStatus.FAILED,
                f"this workspace is owned by host {owner!r}, not this host "
                f"({self_name!r}) — run this preflight from {owner!r} instead",
            )
        )

    # 4. the named peer is declared
    if peer_declared:
        checks.append(
            Check(
                "the named peer is declared",
                CheckStatus.PASSED,
                f"peer {peer_name!r} is declared in hosts.toml",
            )
        )
    else:
        checks.append(
            Check(
                "the named peer is declared",
                CheckStatus.FAILED,
                f"peer {peer_name!r} is not declared in hosts.toml",
            )
        )

    # 5. the peer answers
    if probe_result is None:
        checks.append(
            Check("the peer answers", CheckStatus.FAILED, "the peer was never probed")
        )
    elif isinstance(probe_result, TransportOutcome):
        checks.append(
            Check(
                "the peer answers",
                CheckStatus.INDETERMINATE,
                f"indeterminate — {_transport_detail(probe_result)}",
                transport_outcome=probe_result,
            )
        )
    else:
        checks.append(Check("the peer answers", CheckStatus.PASSED, "the peer responded"))

    # 6. the peer's declared name differs from this host's
    name = "the peer's declared name differs from this host's"
    if isinstance(probe_result, SelfNameCollision):
        # This check's own question IS the collision, so it names it rather
        # than reporting the generic "cannot evaluate" every other
        # peer-dependent check gives for the same input.
        checks.append(
            Check(
                name,
                CheckStatus.FAILED,
                f"the peer declares its own name as {probe_result.peer_self_name!r}, "
                "the same name this host declares",
            )
        )
    else:
        unavailable = _peer_payload_unavailable(name, probe_result)
        checks.append(
            unavailable
            if unavailable is not None
            else Check(name, CheckStatus.PASSED, name)
        )

    # 7. the peer has the group configured with existing member repo roots
    name = "the peer has the group configured with existing member repo roots"
    unavailable = _peer_payload_unavailable(name, probe_result)
    if unavailable is not None:
        checks.append(unavailable)
    elif not probe_result.group_configured:
        checks.append(
            Check(name, CheckStatus.FAILED, "the peer does not have this group configured")
        )
    else:
        missing = [m.name for m in probe_result.members if not m.repo_root_exists]
        if missing:
            checks.append(
                Check(
                    name,
                    CheckStatus.FAILED,
                    "the peer is missing member repo root(s): " + ", ".join(missing),
                )
            )
        else:
            checks.append(
                Check(
                    name,
                    CheckStatus.PASSED,
                    "the peer has the group configured with every member repo root present",
                )
            )

    # 8. the peer's harness account binding matches this end's
    name = "the peer's harness account binding matches this end's"
    unavailable = _peer_payload_unavailable(name, probe_result)
    if unavailable is not None:
        checks.append(unavailable)
    elif probe_result.account == self_account:
        checks.append(
            Check(name, CheckStatus.PASSED, f"account binding matches ({self_account!r})")
        )
    else:
        checks.append(
            Check(
                name,
                CheckStatus.FAILED,
                f"the peer's harness account binding is {probe_result.account!r}, "
                f"this end's is {self_account!r}",
            )
        )

    # 9. the slug is free on the peer, or present there and owned by this host
    name = "the slug is free on the peer, or present there and owned by this host"
    unavailable = _peer_payload_unavailable(name, probe_result)
    if unavailable is not None:
        checks.append(unavailable)
    elif not probe_result.workspace_exists:
        checks.append(Check(name, CheckStatus.PASSED, f"slug {slug!r} is free on the peer"))
    elif probe_result.workspace_owner == self_name:
        checks.append(
            Check(
                name,
                CheckStatus.PASSED,
                f"slug {slug!r} exists on the peer but is owned by this host",
            )
        )
    else:
        checks.append(
            Check(
                name,
                CheckStatus.FAILED,
                f"slug {slug!r} exists on the peer, owned by "
                f"{probe_result.workspace_owner!r}",
            )
        )

    # 10. every member declares an excluded set
    undeclared = [m.name for m in members if m.excluded is None]
    if undeclared:
        checks.append(
            Check(
                "every member declares an excluded set",
                CheckStatus.FAILED,
                "member(s) never declared an excluded set: " + ", ".join(undeclared),
            )
        )
    else:
        checks.append(
            Check(
                "every member declares an excluded set",
                CheckStatus.PASSED,
                "every member declares an excluded set",
            )
        )

    # 11. the conversations rooted here are enumerated
    if conversations is None:
        checks.append(
            Check(
                "the conversations rooted here are enumerated",
                CheckStatus.FAILED,
                "the conversations rooted here could not be enumerated",
            )
        )
    else:
        checks.append(
            Check(
                "the conversations rooted here are enumerated",
                CheckStatus.PASSED,
                f"{len(conversations)} conversation(s) enumerated",
            )
        )

    verdict = (
        Verdict.WOULD_TRANSFER
        if all(c.status is CheckStatus.PASSED for c in checks)
        else Verdict.NOT_CLEAN
    )

    regenerated = tuple(m for m in members if m.excluded)

    return PreflightResult(
        checks=tuple(checks),
        verdict=verdict,
        conversations=conversations or (),
        regenerated=regenerated,
    )
