"""Reconciliation: correct the window record to tmux, on window id alone.

`reconcile` is the pure decision at the center of this module — it matches
each recorded :class:`~camp.group.window_record.WindowEntry` to a live
:class:`~camp.launch.tmux.TmuxWindow` by `window_id` and nothing else. A
window's name is what an operator changes on a whim (`camp: window record:
renamed`); its id is what tmux itself never reassigns to a different window
within a session's lifetime. Matching on name would let two windows that
happen to share a label swap identities, and would fail to notice a window
whose name is untouched but whose id no longer exists.

`reconcile_workspace_record` is the locked, effectful wrapper every caller
outside a test reaches for: it reads the record, asks the tmux seam what is
live, calls the pure `reconcile`, and writes back only when something
changed — mirroring the read-modify-write shape of
`camp.group.window_record.record_window_entry_unlocked`, under the same
`reconcile_lock`.

`render_changes` turns the closed set of `Change` values into the lines the
door and stop print to the operator, escaped as a whole line each through
the same `printable_path` the door already uses for that reason — see
`lesson/escape-the-composed-output-line-not-a-chosen-subset-of-its-fields`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from ..group.manifest import LockTimeout, reconcile_lock
from ..group.window_record import (
    WindowEntry,
    read_window_record,
    window_record_path_for,
    write_window_record,
)
from .recovery import printable_path
from .tmux import UNANSWERED, TmuxWindow


@dataclass(frozen=True)
class Change:
    """Base of the closed set of corrections `reconcile` can report. Never
    returned itself."""


@dataclass(frozen=True)
class Dropped(Change):
    """A recorded entry whose window id is no longer live.

    `conversation_id` and `command_line` mirror the dropped
    :class:`WindowEntry`'s own fields — exactly one is set — so
    :func:`render_changes` can name what the operator is losing without
    needing the entry itself, which `reconcile` does not return.
    """

    window_id: str
    name: str
    conversation_id: str | None = None
    command_line: str | None = None


@dataclass(frozen=True)
class Renamed(Change):
    """A recorded entry whose live window carries a different name now."""

    window_id: str
    old: str
    new: str


@dataclass(frozen=True)
class ReconcileResult:
    """What the pure `reconcile` decides: the corrected entries, in the
    recorded order, and the changes that produced them."""

    entries: tuple[WindowEntry, ...]
    changes: tuple[Change, ...]


def reconcile(
    entries: "list[WindowEntry] | tuple[WindowEntry, ...]",
    live: "list[TmuxWindow] | tuple[TmuxWindow, ...]",
) -> ReconcileResult:
    """Match *entries* to *live* on `window_id` alone.

    An entry whose id has no live match is dropped. An entry whose id
    matches survives regardless of how its recorded name, cwd, or tmux
    index compare to the live window — only `cwd` and `conversation_id` /
    `command_line` are load-bearing identity elsewhere, and neither is ever
    touched here. A live window carrying no entry produces nothing: this
    function only ever corrects what was already recorded, never adds to
    it. Record order is preserved for every surviving entry.
    """
    live_by_id = {window.window_id: window for window in live}
    result_entries: list[WindowEntry] = []
    changes: list[Change] = []
    for entry in entries:
        window = live_by_id.get(entry.window_id)
        if window is None:
            changes.append(
                Dropped(
                    window_id=entry.window_id,
                    name=entry.name,
                    conversation_id=entry.conversation_id,
                    command_line=entry.command_line,
                )
            )
            continue
        if window.name != entry.name:
            changes.append(Renamed(window_id=entry.window_id, old=entry.name, new=window.name))
            entry = replace(entry, name=window.name)
        result_entries.append(entry)
    return ReconcileResult(entries=tuple(result_entries), changes=tuple(changes))


@dataclass(frozen=True)
class ReconcileOutcome:
    """Base of the closed set of outcomes `reconcile_workspace_record` can
    report. Never returned itself."""


@dataclass(frozen=True)
class Reconciled(ReconcileOutcome):
    """The record was read and (possibly) corrected against tmux.

    `changes` is empty when the record already agreed with tmux — the
    wrapper made no write in that case, and `entries` is simply what was
    already on disk (or, for a missing record, empty).
    """

    entries: tuple[WindowEntry, ...]
    changes: tuple[Change, ...]


@dataclass(frozen=True)
class NotReconciled(ReconcileOutcome):
    """Reconciliation could not run: an unparseable record, or tmux did not
    answer (or answered that the session does not exist). `reason` names
    what happened, and — for a corrupt record — the record's path."""

    reason: str


#: The bound `reconcile_workspace_record` waits for the workspace lock
#: before giving up — a few seconds, chosen so a caller stuck behind the
#: background provisioner's own long-held lock (held across every member's
#: `git worktree add`) reports "not reconciled" instead of hanging. Callers
#: that need a different bound (tests) pass `lock_timeout` explicitly.
RECONCILE_LOCK_TIMEOUT_SECONDS = 3.0


def reconcile_workspace_record(
    ws_dir: Path,
    session_name: str,
    tmux,
    *,
    lock_timeout: float = RECONCILE_LOCK_TIMEOUT_SECONDS,
) -> ReconcileOutcome:
    """Correct *ws_dir*'s window record to tmux, under the workspace lock.

    A corrupt record is reported and left untouched — reconciliation cannot
    correct what it cannot parse, and must never replace it with an empty
    one (that would turn "camp cannot say what is in this session" into
    "nothing is in this session"). A tmux answer that is `UNANSWERED` or
    `None` (no such session) changes nothing either: "could not tell" must
    never be read as "no windows". Only when `reconcile` reports a non-empty
    `changes` does this write, through `write_window_record`, still inside
    the lock.

    The lock acquire is bounded (`lock_timeout`, a few seconds) rather than
    the provisioner's own unbounded wait: a `camp attach`/`camp stop` caught
    behind the lock the background provisioner holds across every member's
    `git worktree add` must still connect/stop rather than hang silently. A
    lock that could not be acquired in time is reported as `NotReconciled`,
    the record untouched, the same as any other reconciliation refusal.
    """
    ws_dir = Path(ws_dir)
    path = window_record_path_for(ws_dir)
    try:
        with reconcile_lock(ws_dir, timeout=lock_timeout):
            record = read_window_record(path)
            if record.status == "corrupt":
                return NotReconciled(reason=f"camp: window record at {path} could not be read; not reconciled")

            listing = tmux.list_windows(session_name)
            if listing is UNANSWERED:
                return NotReconciled(
                    reason=(
                        f"camp: tmux did not answer for session {session_name!r}; "
                        f"window record at {path} not reconciled"
                    )
                )
            if listing is None:
                return NotReconciled(
                    reason=(
                        f"camp: no such tmux session {session_name!r}; "
                        f"window record at {path} not reconciled"
                    )
                )
            if listing.dropped > 0:
                # A live window whose row failed to split is not "closed" —
                # `reconcile` has no way to match it and would drop its
                # recorded entry for good. Refuse instead of guessing.
                return NotReconciled(
                    reason=(
                        f"camp: window record at {path} not reconciled; tmux listed "
                        f"{listing.dropped} window(s) camp could not read"
                    )
                )

            result = reconcile(list(record.entries), listing.windows)
            if result.changes:
                write_window_record(path, list(result.entries))
            return Reconciled(entries=result.entries, changes=result.changes)
    except LockTimeout:
        return NotReconciled(
            reason=f"camp: window record at {path} is locked by another camp process; not reconciled"
        )


def render_reconcile_lines(outcome: ReconcileOutcome) -> list[str]:
    """The lines a :func:`reconcile_workspace_record` outcome prints: one per
    change for a :class:`Reconciled`, or the single reason line for a
    :class:`NotReconciled`.

    The one rendering both callers share — the door prints them to stderr
    before its own outcome line (`camp.cli.session._print_reconcile_outcome`)
    and the stop engine hands them to its `emit` sink before the preview
    (`camp.launch.stop_workspace.stop_workspace`) — so the two surfaces can
    never disagree on what a reconciliation says. Every line is escaped whole
    through `printable_path`, like every other line this module composes.
    """
    if isinstance(outcome, Reconciled):
        return render_changes(outcome.changes)
    if isinstance(outcome, NotReconciled):
        return [printable_path(outcome.reason)]
    raise TypeError(f"camp: unrenderable reconcile outcome {outcome!r}")


def render_changes(changes: "list[Change] | tuple[Change, ...]") -> list[str]:
    """One line per change, escaped whole through `printable_path`.

    A dropped entry names its conversation id, when it has one, so the
    operator keeps the one fact needed to resume it from the harness's own
    transcript store; a dropped entry with no conversation names its
    command line instead. A renamed entry names its old and new name.
    """
    lines = []
    for change in changes:
        if isinstance(change, Dropped):
            if change.conversation_id is not None:
                detail = f"conversation {change.conversation_id}"
            else:
                detail = f"command {change.command_line}"
            line = (
                f'camp: window record: dropped {change.window_id} "{change.name}" '
                f"(closed in tmux; {detail})"
            )
        elif isinstance(change, Renamed):
            line = (
                f'camp: window record: renamed {change.window_id} '
                f'"{change.old}" -> "{change.new}"'
            )
        else:  # pragma: no cover - closed set, guards a future member
            raise TypeError(f"camp: unrenderable window record change {change!r}")
        lines.append(printable_path(line))
    return lines
