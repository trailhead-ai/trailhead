"""The pure classifier: a group's workspaces plus a tmux enumeration, turned
into rows.

No I/O. :func:`classify_sessions` takes the workspace list and a
:class:`~camp.launch.stop.SessionListing` (or the stop engine's own
``UNANSWERED`` sentinel) as arguments and returns a :class:`Classification` —
every branch below is reachable by varying one of those two arguments, and
nothing here calls tmux, reads a file, or reads a record.

Precedence, the deliverable this module owes: a tmux session whose name is a
workspace's own derived name (:func:`~camp.launch.naming.workspace_session_name`)
is that workspace's session, full stop. Only what tmux reports that is NOT
claimed by any workspace this way is tested against the retired
one-session-per-conversation form (:func:`~camp.launch.naming.is_retired_session_name`).

The claiming set and the reporting set are deliberately NOT the same set. A
group-scoped caller reports rows for one group, but tmux is host-wide, so a
sibling group's LIVE session would otherwise be claimed by nobody, fall
through to the retired-form check — which any slug that is or ends in 8 hex
characters satisfies — and be reported as a leftover. That is not a
cosmetic misreading: the operator's remedy for a leftover is
``tmux kill-session``, so it would aim a destructive action at another
group's in-use session. ``host_claimed_names`` is therefore the whole host's
claiming set, supplied by the caller (this module does no I/O), while
*workspaces* remains only what the caller was asked to report on.
Resemblance grants no adoption: a leftover session whose name merely
*contains* a workspace's slug, or a retired session whose component happens
to match one, is never folded into that workspace's row — only an exact
derived-name match claims a session, so a workspace's own state can never be
altered by a session it did not itself name. Anything left over that is
neither a workspace's own session nor a retired-form name produces no row at
all: camp has no vocabulary for a session it cannot attribute to either
scheme.

Disclosure scope: a leftover (unmanaged) session's name carries the
workspace-name component it was derived from, and camp already narrowed
group-scoped session queries once to keep one group's project names out of
another's view (see ``docs/design/cross-group-cross-account-listing.md``).
:data:`DisclosureScope.GROUP` preserves that boundary — the caller learns
only how many leftovers there are. :data:`DisclosureScope.WIDENED` is for a
caller that has already opted into seeing across the whole host (an
``--all-groups`` / ``--all-hosts`` axis) and gets the rows themselves. The
scope argument changes ONLY the leftover half of the answer: workspace rows
are identical under both scopes, because a workspace's own state was never a
disclosure question.

The tmux enumeration's own tri-state carries through: when it is
``UNANSWERED`` (tmux never answered), every workspace row classifies
``unknown`` and no unmanaged rows are produced — camp cannot claim knowledge
of a leftover it never saw. ``SessionListing.dropped`` (a row tmux printed
that could not be parsed) is carried into :class:`Classification.dropped`
rather than swallowed, so a caller can tell "a row was unparseable" apart
from "the answer was empty".
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Sequence

from .naming import is_retired_session_name, workspace_session_name
from .stop import SessionListing, _Unanswered

#: A workspace's tmux session carries no state yet.
STATE_NONE = "none"
#: A workspace's tmux session is up, with a window count.
STATE_RUNNING = "running"
#: A tmux session claimed by no workspace, matching the retired form.
STATE_UNMANAGED = "unmanaged"
#: The tmux enumeration never answered; nothing is known.
STATE_UNKNOWN = "unknown"


class DisclosureScope(enum.Enum):
    """Which leftover sessions a caller is allowed to see named.

    See the module docstring — this decides only the shape of the
    UNMANAGED half of the answer; workspace rows never vary with it.
    """

    #: A single group's own listing: leftovers are counted, never named.
    GROUP = "group"
    #: A caller that already spans groups (or the whole host): leftovers
    #: come back as rows.
    WIDENED = "widened"


@dataclass(frozen=True)
class Workspace:
    """The minimum a workspace must state to be classified: which group and
    slug its session name derives from, and the path a row reports."""

    group: str
    slug: str
    path: str


@dataclass(frozen=True)
class WorkspaceSession:
    """One workspace's classified row.

    ``windows`` is populated only when ``state`` is :data:`STATE_RUNNING`;
    every other state carries ``None`` because there is no session to count
    windows on. ``activity`` mirrors that: the matched tmux session's own
    ``#{session_activity}`` epoch second, `None` when there is no matched
    session to read it from — the tmux half of a workspace's "last touched"
    instant (see `camp.launch.lasttouched`, which combines it with the
    caller's own worktree/manifest reads).
    """

    slug: str
    path: str
    state: str
    windows: int | None = None
    activity: int | None = None


@dataclass(frozen=True)
class UnmanagedSession:
    """One leftover tmux session, claimed by no workspace. Carries only what
    tmux itself reported — no slug, no path, because no workspace owns it.
    ``activity`` is the session's own ``#{session_activity}``, same as
    :attr:`WorkspaceSession.activity`."""

    name: str
    windows: int
    activity: int | None = None


@dataclass(frozen=True)
class Classification:
    """The classifier's whole answer.

    ``workspaces`` preserves the input workspace order. ``unmanaged`` is
    empty under :data:`DisclosureScope.GROUP` regardless of how many
    leftovers exist; ``unmanaged_count`` always states that number, so a
    caller comparing the two scopes' counts is comparing the same fact.
    ``dropped`` is the enumeration's own count, carried through unchanged
    (0 when the enumeration never answered, since nothing about a dropped
    row is known in that case either).
    """

    workspaces: tuple[WorkspaceSession, ...]
    unmanaged: tuple[UnmanagedSession, ...]
    unmanaged_count: int
    dropped: int


#: The human row's field for a row that has no value to print there — a
#: leftover session has no workspace path, and a no-group fallback row has
#: no derivable session name and so no state. Data says ``None``; only the
#: human rendering says ``-``.
HUMAN_ABSENT = "-"


def format_state(state: str | None, windows: int | None) -> str:
    """The state field as `camp attach`'s workspace picker prints it
    (:class:`~camp.attach.door_target.WorkspaceCandidate.state_text`, built
    from `cmd_ls_group`'s own entries).

    :data:`STATE_RUNNING` renders with its window count appended
    (``running:3``) — the count is the one piece of size information in the
    row. A row with no state at all (the no-group registry fallback, or a
    row relayed by a camp predating the column) renders :data:`HUMAN_ABSENT`.

    `camp list`/`ls`'s own SESSIONS column renders through
    :func:`format_sessions_cell` instead — a bare window count, since the
    table already states a row's `STATE_RUNNING`-ness structurally (a
    session row vs. a `0`/`?` one) rather than needing it spelled out in the
    cell's own text.
    """
    if not state:
        return HUMAN_ABSENT
    if state == STATE_RUNNING and windows is not None:
        return f"{state}:{windows}"
    return state


def format_sessions_cell(state: str | None, windows: int | None) -> str:
    """`camp list`/`ls`'s SESSIONS column: the running (or unmanaged) session's
    window count, ``0`` for a workspace with no session, and ``?`` when
    nothing is known — tmux never answered (:data:`STATE_UNKNOWN`), or the
    row carries no state at all (the no-group fallback, or a row relayed by
    a camp predating the state column).

    :data:`STATE_UNMANAGED` renders its window count exactly like
    :data:`STATE_RUNNING` does — an unmanaged row IS a live tmux session,
    just one no workspace claims, so its window count is exactly as known.
    """
    if not state or state == STATE_UNKNOWN:
        return "?"
    if state == STATE_NONE:
        return "0"
    if windows is None:
        return "?"
    return str(windows)


def format_last_touched(ts: float | None, *, now: float) -> str:
    """`camp list`/`ls`'s LAST TOUCHED column: a compact relative age against
    *now* (injected so a test never depends on wall-clock time), or
    :data:`HUMAN_ABSENT` when *ts* is `None` — nothing observable about this
    workspace (see `camp.launch.lasttouched.workspace_last_touched`).

    Buckets: under a minute is ``just now``; under an hour, whole minutes
    (``5m ago``); under a day, whole hours (``3h ago``); under a week, whole
    days (``2d ago``); otherwise whole weeks (``3w ago``). A *ts* in the
    future (clock skew between two camp-answering machines) falls in the
    first bucket, ``just now``, rather than printing a negative duration.
    """
    if ts is None:
        return HUMAN_ABSENT
    elapsed = now - ts
    if elapsed < 60:
        return "just now"
    if elapsed < 3600:
        return f"{int(elapsed // 60)}m ago"
    if elapsed < 86400:
        return f"{int(elapsed // 3600)}h ago"
    if elapsed < 86400 * 7:
        return f"{int(elapsed // 86400)}d ago"
    return f"{int(elapsed // (86400 * 7))}w ago"


#: `camp list`/`ls`'s table headers, local or narrow-scoped (no GROUP column).
WORKSPACE_TABLE_HEADERS = ("WORKSPACE", "SESSIONS", "LAST TOUCHED")
#: The same headers, widened with a GROUP column — the `--all-groups`/`-g`
#: axis, and any `--host`/`--all-hosts` answer that spans groups.
WORKSPACE_TABLE_HEADERS_WITH_GROUP = WORKSPACE_TABLE_HEADERS + ("GROUP",)


def workspace_row_cells(row: dict, *, now: float, show_group: bool) -> list[str]:
    """One `camp list`/`ls` row's table cells: WORKSPACE, SESSIONS, LAST
    TOUCHED, and — when *show_group* — GROUP. The one place a workspace or
    unmanaged row (local, relayed, or merged) is turned into its table
    cells, so every human surface reads a row identically.

    Every field here is peer-suppliable — a relayed row's `slug`,
    `tmux_session`, `group`, and `last_touched` can be anything the far camp
    chooses to send — so every cell is escaped with `printable_path` before
    :func:`~camp.launch.table.render_table` ever sees it: a control
    character in any one of them must never forge a second table row.

    `last_touched` is read as either representation a caller may hold it in:
    an epoch float (a local row, built straight from `cmd_ls_group`, never
    round-tripped through JSON) or an ISO-8601 UTC string (a relayed or
    merged row, read back from a `--json` answer) — `None`, or a string
    that fails to parse (an older relayed row lacking the field), renders
    :data:`HUMAN_ABSENT`.

    Raises `KeyError` when `slug` or `workspace_path` is missing — the two
    keys every row (local or relayed) is guaranteed to carry — so a caller
    can degrade that one row rather than fail the whole listing.
    `workspace_path` is required only as that same completeness check; the
    path itself is never rendered — `camp pwd` and `--json` carry it.
    """
    from .lasttouched import from_iso_utc
    from .recovery import printable_path

    _ = row["workspace_path"]  # completeness check only — never rendered
    first = row["slug"] if row["slug"] is not None else row["tmux_session"]

    last_touched = row.get("last_touched")
    if isinstance(last_touched, str):
        last_touched = from_iso_utc(last_touched)

    cells = [
        printable_path(str(first)),
        printable_path(format_sessions_cell(row.get("state"), row.get("window_count"))),
        printable_path(format_last_touched(last_touched, now=now)),
    ]
    if show_group:
        cells.append(printable_path(row.get("group") or HUMAN_ABSENT))
    return cells


def format_unmanaged_summary(count: int) -> str:
    """The one line a group-scoped answer owes about leftover sessions it
    counted but did not name. Agrees in number with *count*, and names the
    widening an operator asks for to see them."""
    sessions = "session" if count == 1 else "sessions"
    return f"{count} unmanaged camp {sessions} — `camp list -g` to name them"


def classify_sessions(
    workspaces: Sequence[Workspace],
    listing: SessionListing | _Unanswered,
    *,
    scope: DisclosureScope,
    host_claimed_names: Sequence[str] = (),
) -> Classification:
    """Classify *workspaces* against tmux's *listing*. See the module docstring.

    *host_claimed_names* is the claiming set: every session name a workspace
    ANYWHERE on this host derives, which is a superset of the names
    *workspaces* itself derives. A session in it is some workspace's own
    session and so is never a leftover, whether or not the workspace that
    named it is one this call was asked about. Supplying it is what keeps
    the claiming set and the reporting set separate — see the module
    docstring — and the caller reads it, because this classifier does no
    I/O. Omitted, every session not claimed by *workspaces* is a leftover
    candidate, which is the right answer only for a caller that already
    knows it holds the whole host.
    """
    if isinstance(listing, _Unanswered):
        rows = tuple(
            WorkspaceSession(slug=w.slug, path=w.path, state=STATE_UNKNOWN)
            for w in workspaces
        )
        return Classification(
            workspaces=rows, unmanaged=(), unmanaged_count=0, dropped=0
        )

    by_name = {session.name: session for session in listing.sessions}
    claimed: set[str] = set(host_claimed_names)

    workspace_rows: list[WorkspaceSession] = []
    for w in workspaces:
        name = workspace_session_name(w.group, w.slug)
        session = by_name.get(name)
        if session is not None:
            claimed.add(name)
            workspace_rows.append(
                WorkspaceSession(
                    slug=w.slug,
                    path=w.path,
                    state=STATE_RUNNING,
                    windows=session.windows,
                    activity=session.activity,
                )
            )
        else:
            workspace_rows.append(
                WorkspaceSession(slug=w.slug, path=w.path, state=STATE_NONE)
            )

    leftover_rows: list[UnmanagedSession] = []
    for session in listing.sessions:
        if session.name in claimed:
            continue
        if not is_retired_session_name(session.name):
            continue
        leftover_rows.append(
            UnmanagedSession(name=session.name, windows=session.windows, activity=session.activity)
        )

    unmanaged = tuple(leftover_rows) if scope is DisclosureScope.WIDENED else ()

    return Classification(
        workspaces=tuple(workspace_rows),
        unmanaged=unmanaged,
        unmanaged_count=len(leftover_rows),
        dropped=listing.dropped,
    )
