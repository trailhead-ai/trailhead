"""The numbered picker over attachable sessions.

`camp attach` with no reference offers a numbered choice among what is
actually running. This module is the presentation-and-selection loop, plus
one convenience builder for the local machine's row set — it never attaches
anything itself, the same discipline `attach.resolve` and `attach.ownership`
already keep.

TWO INPUT PATHS, ON PURPOSE. :func:`pick_session` takes a :class:`PoolResult`
— rows the caller already built — rather than enumerating anything itself,
because the bare local picker and the `-a` cross-machine picker feed it from
different places: the local form calls :func:`local_pool` (below) against
this machine's own harness and tmux seams, while `-a` assembles rows from
every declared machine's own answer and has no single local pool to read.
Hard-coding "enumerate the local pool" as the only way in would give `-a`
nowhere to feed cross-host rows.

SAME POOL THE REFERENCE FORM RESOLVES AGAINST. :func:`local_pool` reads
`launch.recovery.session_candidates` — the identical union `attach.resolve`
resolves a reference against — and applies the identical narrowing:
liveness read straight off the candidate, then
`attach.ownership.resolve_ownership`, in that order. A session the ref form
would refuse to resolve to (stopped, or a pane camp does not own) never
becomes a row here either; this module does not re-derive that rule, it
drives the same two checks the ref form drives.

ORDERING IS `session_candidates`'s OWN. It sorts freshest-first already
(`launch/recovery.py`'s own docstring); this module preserves that order
rather than re-sorting, so the picker's "most recently active first" is a
property of the shared pool, not a second opinion layered on top of it.

STALENESS BETWEEN LISTING AND SELECTION — DECIDED, NOT LEFT OPEN. A listed
session can stop, or lose ownership, in the gap between the numbered list
being drawn and the operator's line landing. This module does not revalidate
either at selection time: :func:`pick_session` hands back the
:class:`SessionCandidate` it captured when the pool was built, unchanged. A
second read here would still race the same gap one read later, and this
module has no handoff seam to ask anyway (`attach.resolve` and
`attach.ownership` are its only collaborators, not `camp.host.handoff`). The
message a caller sees on a session that died in that gap is therefore
whichever the *handoff* produces attempting to reach a pane that is no
longer there — camp's own liveness check is not repeated for it. Revalidating
belongs at the handoff call site, if it belongs anywhere, because that is the
only place close enough to selection for a second check to narrow the gap
rather than just relocate it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import IO

from ..launch import recovery
from ..launch.recovery import SessionCandidate
from .ownership import Owned, resolve_ownership


@dataclass(frozen=True)
class Row:
    """One attachable session, ready for the picker to present.

    ``machine`` is supplied by the caller — this module never guesses it, for
    the same reason it never enumerates on its own: a cross-host picker
    composes rows from more than one camp's own answer, and only the caller
    holding that answer knows which machine it came from.
    """

    machine: str
    group: str | None
    slug: str
    candidate: SessionCandidate


@dataclass(frozen=True)
class PoolReady:
    """Rows the picker may present, in presentation order."""

    rows: tuple[Row, ...]


@dataclass(frozen=True)
class PoolUnreadable:
    """The pool this picker would present could not be enumerated at all.

    Distinct from an empty :class:`PoolReady` on purpose — the design this
    module builds to (`docs/design/attaching-reaches-a-running-session-on-any-machine.md`,
    "the running-session collection could not be read") is explicit that an
    unreadable pool is never allowed to present as "nothing is running".
    """

    reason: str


PoolResult = PoolReady | PoolUnreadable


@dataclass(frozen=True)
class Picked:
    """The operator chose this row."""

    row: Row


@dataclass(frozen=True)
class NothingToOffer:
    """The pool was read successfully and held nothing. No prompt shown."""


PickResult = Picked | NothingToOffer | PoolUnreadable


def local_pool(
    *,
    harness,
    tmux,
    transcripts,
    live_records,
    groups,
    env,
    machine: str,
    now=None,
) -> PoolResult:
    """This machine's row set: live, camp-owned candidates only.

    See the module docstring — this reads the same pool
    `attach.resolve.resolve_attach_ref` resolves against and applies the same
    liveness-then-ownership narrowing, so the two surfaces never disagree
    about what "attachable" means.

    Any failure reading the pool — the harness's transcripts or live records
    could not be enumerated, or the multiplexer could not answer at all — is
    caught here and reported as :class:`PoolUnreadable`, naming what failed,
    rather than surfacing as an exception or silently yielding an empty pool.
    """
    try:
        candidates = recovery.session_candidates(
            transcripts=transcripts,
            live_records=live_records,
            groups=groups,
            env=env,
            now=now,
        )

        rows = []
        for candidate in candidates:
            if not candidate.live:
                continue
            ownership = resolve_ownership(harness, tmux, candidate)
            if not isinstance(ownership, Owned):
                continue
            rows.append(_make_row(candidate, machine=machine, groups=groups, env=env))
    except Exception as exc:  # the pool itself could not be read
        return PoolUnreadable(reason=str(exc))

    return PoolReady(rows=tuple(rows))


def _make_row(candidate: SessionCandidate, *, machine: str, groups, env) -> Row:
    if candidate.root is None:
        return Row(
            machine=machine,
            group=None,
            slug=candidate.derived_name.removeprefix("camp-"),
            candidate=candidate,
        )

    from ..cli.session import _attribute_session

    attribution = _attribute_session(candidate.root, groups, env=env)
    slug = recovery.derive_name_component(candidate.root, groups, env=env)
    return Row(machine=machine, group=attribution["group"], slug=slug, candidate=candidate)


def pick_session(
    pool: PoolResult,
    *,
    stdin: IO[str],
    stdout: IO[str],
    isatty: bool,
) -> PickResult:
    """Present *pool* as a numbered list and read one choice.

    Zero, one, and many rows each render their own outcome (see the test
    contract for this module): zero states so and returns without prompting;
    one still prompts rather than auto-attaching; many is the ordinary
    numbered list.

    *isatty* is checked before ``stdin`` is touched at all, so a caller
    without a terminal gets a stated refusal and this function reads nothing
    — not even to discover there was nothing to read.

    Re-prompts, rather than exits, on blank input, a non-numeric line, and an
    out-of-range number. A valid choice after any of those still selects.
    """
    if isinstance(pool, PoolUnreadable):
        return pool

    rows = pool.rows
    if not rows:
        return NothingToOffer()

    if not isatty:
        return PoolUnreadable(
            reason="no terminal to prompt into — use `camp attach <ref>` instead"
        )

    while True:
        stdout.write("Running sessions:\n")
        for index, row in enumerate(rows, start=1):
            stdout.write(f"  {index}) {row.machine or 'this machine'}  {row.group or '-'}  {row.slug}\n")
        stdout.write("> ")
        stdout.flush()

        line = stdin.readline()
        if not line:
            return PoolUnreadable(reason="no input read — end of input reached")

        choice = line.strip()
        if not choice.isdigit():
            continue

        index = int(choice)
        if index < 1 or index > len(rows):
            continue

        return Picked(row=rows[index - 1])
