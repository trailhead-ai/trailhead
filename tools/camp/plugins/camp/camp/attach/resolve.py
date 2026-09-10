"""Resolve an operator's attach reference without attaching to anything.

This is the seam `camp attach -a` probes on every machine and the answer
`--host` relies on the far side producing (:mod:`camp.attach.ownership`'s
own module docstring, and the attach slice's design record, spell out why).
It is data-to-data plus the `harness` and `tmux` seams `resolve_ownership`
itself takes — it never touches `camp.host.handoff`, because a resolve-only
answer that could attach is not a resolve-only answer.

RESOLVE OVER THE FULL POOL, THEN NARROW THE ANSWER. `resolve_session_ref`
(`launch/recovery.py`) is reached unforked, over the full `transcripts ∪
live_records` pool — never a pool pre-filtered to live, camp-owned sessions.
Narrowing the *inputs* before matching collapses "the named session exists
but is stopped" and "the named session was never found" into one identical
`NoMatch`, which is the bug an assumption-prover proved out for this task:
both cases came back `NoMatch(pool_size=1)`, indistinguishable, despite the
operator's next step differing completely between them.

Narrowing happens on the *answer* instead, as two post-hoc checks against the
one `Resolved` candidate the (unforked) resolver returns:

- liveness is read straight off `candidate.live` — this is what makes
  :class:`NotRunning` distinguishable from :class:`NoMatch`, and it is
  checked BEFORE ownership: a stopped session owns no tmux pane at all, so
  asking ownership first would read as "camp does not own this" and produce
  the exact collapse this module exists to avoid.
- ownership is `camp.attach.ownership.resolve_ownership(harness, tmux,
  candidate)`, called once the candidate is known to be live. Anything short
  of :class:`~camp.attach.ownership.Owned` (a foreign pane, or one tmux could
  not describe) answers :class:`NoMatch` — camp will not offer to attach the
  operator to a pane it did not compose, and "no-match" is the outcome the
  parent task assigns that case.

The resolver this module calls is the same one `camp kill` calls
(`launch/stop.py`'s own `stop_session`) — mutating the shared matcher moves
both verbs' answers together, because both are the same call into the same
seam.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..launch import recovery
from ..launch.recovery import Ambiguous as _ResolverAmbiguous
from ..launch.recovery import NoMatch as _ResolverNoMatch
from ..launch.recovery import Resolved as _ResolverResolved
from ..launch.recovery import SessionCandidate
from .ownership import Owned, resolve_ownership


@dataclass(frozen=True)
class AttachResolution:
    """Base of the closed set of attach-resolve outcomes. Never returned itself."""


@dataclass(frozen=True)
class Resolved(AttachResolution):
    """The ref addressed exactly one session, live and camp-owned. Attachable."""

    candidate: SessionCandidate


@dataclass(frozen=True)
class Ambiguous(AttachResolution):
    """The ref addressed more than one session on this machine."""

    candidates: tuple[SessionCandidate, ...]


@dataclass(frozen=True)
class NoMatch(AttachResolution):
    """The ref addressed nothing camp can attach to: no session matched, or the
    one that did is not one camp owns."""

    pool_size: int


@dataclass(frozen=True)
class NotRunning(AttachResolution):
    """The ref addressed exactly one session that exists but is not live.

    Distinct from :class:`NoMatch` on purpose: the operator's next move
    differs — resume it, versus check the reference again.
    """

    candidate: SessionCandidate


def resolve_attach_ref(
    ref,
    *,
    harness,
    tmux,
    transcripts,
    live_records,
    groups,
    env,
    now=None,
) -> AttachResolution:
    """Resolve *ref* against this machine's pool. Attaches nothing.

    See the module docstring for the pool-then-answer narrowing this
    performs, and why the direction matters.
    """
    resolution = recovery.resolve_session_ref(
        ref,
        transcripts=transcripts,
        live_records=live_records,
        groups=groups,
        env=env,
        now=now,
    )

    if isinstance(resolution, _ResolverNoMatch):
        return NoMatch(pool_size=resolution.pool_size)
    if isinstance(resolution, _ResolverAmbiguous):
        return Ambiguous(candidates=resolution.candidates)

    assert isinstance(resolution, _ResolverResolved)
    candidate = resolution.candidate

    if not candidate.live:
        return NotRunning(candidate)

    ownership = resolve_ownership(harness, tmux, candidate)
    if isinstance(ownership, Owned):
        return Resolved(candidate)

    pool_size = len(
        recovery.session_candidates(
            transcripts=transcripts,
            live_records=live_records,
            groups=groups,
            env=env,
            now=now,
        )
    )
    return NoMatch(pool_size=pool_size)
