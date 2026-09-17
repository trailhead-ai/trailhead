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
    windows on.
    """

    slug: str
    path: str
    state: str
    windows: int | None = None


@dataclass(frozen=True)
class UnmanagedSession:
    """One leftover tmux session, claimed by no workspace. Carries only what
    tmux itself reported — no slug, no path, because no workspace owns it."""

    name: str
    windows: int


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
    """The state field as a human row prints it.

    :data:`STATE_RUNNING` renders with its window count appended
    (``running:3``) — the count is the one piece of size information in the
    row and the human row is the only surface an operator reads, so a bare
    ``running`` drops it on the floor. A row with no state at all (the
    no-group registry fallback, or a row relayed by a camp predating the
    column) renders :data:`HUMAN_ABSENT`.

    Shared by both renderers — ``render_workspace_list``'s local rows and
    ``render_list_row_human``'s relayed or merged ones — so a row reads the
    same whichever machine answered it. A relayed ``running`` row carrying
    no count still renders bare, because there is no count to state.
    """
    if not state:
        return HUMAN_ABSENT
    if state == STATE_RUNNING and windows is not None:
        return f"{state}:{windows}"
    return state


def format_path(path: str | None) -> str:
    """The path field as a human row prints it — :data:`HUMAN_ABSENT` for a
    row that owns no path (an unmanaged session). Shared by both renderers
    for the same reason :func:`format_state` is."""
    return HUMAN_ABSENT if path is None else path


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
) -> Classification:
    """Classify *workspaces* against tmux's *listing*. See the module docstring."""
    if isinstance(listing, _Unanswered):
        rows = tuple(
            WorkspaceSession(slug=w.slug, path=w.path, state=STATE_UNKNOWN)
            for w in workspaces
        )
        return Classification(
            workspaces=rows, unmanaged=(), unmanaged_count=0, dropped=0
        )

    by_name = {session.name: session for session in listing.sessions}
    claimed: set[str] = set()

    workspace_rows: list[WorkspaceSession] = []
    for w in workspaces:
        name = workspace_session_name(w.group, w.slug)
        session = by_name.get(name)
        if session is not None:
            claimed.add(name)
            workspace_rows.append(
                WorkspaceSession(
                    slug=w.slug, path=w.path, state=STATE_RUNNING, windows=session.windows
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
        leftover_rows.append(UnmanagedSession(name=session.name, windows=session.windows))

    unmanaged = tuple(leftover_rows) if scope is DisclosureScope.WIDENED else ()

    return Classification(
        workspaces=tuple(workspace_rows),
        unmanaged=unmanaged,
        unmanaged_count=len(leftover_rows),
        dropped=listing.dropped,
    )
