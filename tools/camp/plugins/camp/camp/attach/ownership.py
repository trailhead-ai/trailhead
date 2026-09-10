"""The attach-time ownership predicate.

Given the harness seam, the `Tmux` seam, and a `SessionCandidate`, answers
whether the candidate is one camp may hand the operator's terminal to. The
answer is reached by driving `launch.stop._is_camp_launched` — the exact
check `camp kill` applies to decide whether it may signal a pane — never by
restating its rule beside it. A change to what camp composes at launch moves
both answers together, because both are the same call into the same seam.

The answer is three-valued, though the predicate underneath is not.
`_is_camp_launched` takes a two-valued `pane_command: str | None` and has no
branch for `_Unanswered` — that third state is `Tmux.pane_command`'s own, and
`launch.stop.stop_session` filters it out *before* ever calling the
ownership predicate (`stop.py`'s own `isinstance(pane, _Unanswered)` guard).
This module carries that same guard, ahead of the predicate, rather than
letting an unanswered pane reach `_is_camp_launched` and blow up inside
`shlex.split`.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..launch.recovery import SessionCandidate
from ..launch.stop import _Unanswered, _is_camp_launched


@dataclass(frozen=True)
class Owned:
    """The pane is one camp itself composed. Attachable."""

    candidate: SessionCandidate


@dataclass(frozen=True)
class Foreign:
    """The pane exists but its start command is not one camp composed."""

    candidate: SessionCandidate


@dataclass(frozen=True)
class Undescribable:
    """The multiplexer could not answer what the pane's start command is."""

    candidate: SessionCandidate


OwnershipAnswer = Owned | Foreign | Undescribable


def resolve_ownership(harness, tmux, candidate: SessionCandidate) -> OwnershipAnswer:
    """Whether *candidate*'s pane, as `tmux` describes it, is camp's own."""
    pane = tmux.pane_command(candidate.derived_name)
    if isinstance(pane, _Unanswered):
        return Undescribable(candidate)
    if _is_camp_launched(harness, candidate, pane):
        return Owned(candidate)
    return Foreign(candidate)
