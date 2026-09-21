"""`camp stop`'s engine, the closed outcome type it reports through, and the
pure classification that builds its preview.

:class:`StopOutcome` mirrors :class:`~camp.launch.door.DoorOutcome`: a frozen
dataclass base that is never returned itself, with one member per outcome.
Everything but :func:`stop_workspace` is pure — only that one function does
I/O, and it does it through the injected `tmux` seam and `emit` sink.

Five members: two successes — :class:`Stopped` (killed and confirmed gone)
and :class:`NotRunning` (nothing to do) — and three failures —
:class:`StillPresent` (killed, but tmux still answers), and the refusals
:class:`RefusedNoWorkspace` and :class:`RefusedTmuxUnanswered`. Every member
carries `slug`, `group`, `tmux_session`, and a :class:`StopPreview`, even
where the preview is empty (`NotRunning` and the refusals never reach the
point where there is anything to preview) — a uniform shape lets the
renderings and `exit_status` key on type alone, the way `door.py` does.

Classification by exclusion, not by name
-----------------------------------------
`classify` decides each window's `kind` by testing whether its live
foreground command is a shell, never by matching the command against a
conversation process name. The assumption-prover measured (2026-09-21,
tmux 3.7c) that a live conversation's foreground command is the installed
harness *version string* (`2.1.278` today, a different one after every
update) and an older, still-live session can report plain `claude` — so a
positive match would go stale on the next update and report a live
conversation as exited, which is the unsafe direction for a preview whose
whole job is to show the operator what a kill is about to cost. Exclusion
fails safe: a recorded conversation whose window is running anything other
than a shell is shown as live, so the operator sees it even if camp does
not recognise the process name.

Three renderings, all pure functions of a :class:`StopOutcome`:

- :func:`render_human` — the human lines `camp stop` prints on stdout, for
  `Stopped`, `NotRunning`, and `StillPresent` (a refusal's message is
  composed by the code that constructs it, exactly as `door.render_human`
  leaves a refusal's message to its constructor). `Stopped` and
  `StillPresent` print the preview — a count line, then one indented line
  per window — before their own outcome line; `NotRunning` prints
  only its one line, since there is nothing to preview. Composed and
  escaped line by line through :func:`~camp.launch.recovery.printable_path`,
  never a chosen subset of a line's fields, so a control character in a
  window's name cannot inject a line the preview never showed — see
  `lesson/escape-the-composed-output-line-not-a-chosen-subset-of-its-fields`.
- :func:`render_json` — the `--json` object, for the same three members.
  Carries the preview as `windows` (count), `live_conversations` (ids),
  `exited_conversations` (ids), `foreground` (names), and `reconciled`.
- :func:`exit_status` — `0` for `Stopped` and `NotRunning`, `1` for
  `StillPresent` and every refusal, keyed by exact type like `door.py`'s
  own table, so a member added without an entry raises instead of silently
  reporting success.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from .naming import workspace_session_name
from .recovery import printable_path
from .stop import POLL_INTERVAL_SECONDS, POLL_TIMEOUT_SECONDS, poll_for_absence
from .tmux import WindowListing
from .window_reconcile import Reconciled, reconcile_workspace_record, render_reconcile_lines


@dataclass(frozen=True)
class PreviewRow:
    """One live tmux window, classified for the stop preview.

    `conversation_id` is set only for the two conversation kinds; a
    `foreground` or `idle` row carries `None`, since neither corresponds to
    a recorded conversation. `kind` is one of `live-conversation`,
    `exited-conversation`, `foreground`, `idle`. `command` is the window's
    foreground command as tmux reported it — the process a `foreground` row
    names as what the operator would lose.
    """

    window_id: str
    name: str
    conversation_id: str | None
    kind: str
    command: str


@dataclass(frozen=True)
class StopPreview:
    """What a stop is about to cost: every live window, classified.

    `reconciled` and `reconcile_note` are not set by `classify` — they
    describe whether the *record* was corrected against tmux before the
    preview was built, which `classify` has no way to know from a window
    list alone. The engine that reconciles then classifies sets them via
    `dataclasses.replace`.

    `listed` is `False` only when the preview's own `list_windows` call (a
    FRESH one, taken after reconciliation) came back `UNANSWERED` or `None`
    — tmux could not say what is in the session, as distinct from "nothing
    is in the session". `windows` is empty either way, so a renderer must
    check `listed`, never bare emptiness, to tell "no windows" apart from
    "could not tell".
    """

    windows: tuple[PreviewRow, ...] = ()
    reconciled: bool = False
    reconcile_note: str | None = None
    listed: bool = True


@dataclass(frozen=True)
class StopOutcome:
    """Base of the closed set of outcomes `stop_workspace` can report. Never returned itself."""


@dataclass(frozen=True)
class Stopped(StopOutcome):
    """The session was killed and tmux confirms it is gone."""

    slug: str
    group: str
    tmux_session: str
    preview: StopPreview


@dataclass(frozen=True)
class NotRunning(StopOutcome):
    """The workspace had no running session; nothing to do."""

    slug: str
    group: str
    tmux_session: str
    preview: StopPreview = field(default_factory=StopPreview)


@dataclass(frozen=True)
class StillPresent(StopOutcome):
    """The kill was issued and, after the poll, tmux still reports the session."""

    slug: str
    group: str
    tmux_session: str
    preview: StopPreview


@dataclass(frozen=True)
class RefusedNoWorkspace(StopOutcome):
    """The given slug names no workspace in the resolved group."""

    slug: str
    group: str
    tmux_session: str
    preview: StopPreview = field(default_factory=StopPreview)


@dataclass(frozen=True)
class RefusedTmuxUnanswered(StopOutcome):
    """tmux did not answer, so the outcome cannot be determined."""

    slug: str
    group: str
    tmux_session: str
    preview: StopPreview = field(default_factory=StopPreview)


#: The common shell basenames camp treats as an idle shell, regardless of
#: environment. `$SHELL`'s basename joins this set in `default_shell_names`
#: — read through an injected mapping, never `os.environ` directly, so a
#: test can vary it without touching the process environment.
_COMMON_SHELL_BASENAMES = frozenset({"sh", "bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh"})


def default_shell_names(env: Mapping[str, str]) -> frozenset[str]:
    """The default `shell_names` set: the common shells plus the basename
    of `$SHELL` in *env*, when set."""
    names = set(_COMMON_SHELL_BASENAMES)
    shell = env.get("SHELL")
    if shell:
        names.add(os.path.basename(shell))
    return frozenset(names)


#: The module's default `shell_names`, computed once from the real process
#: environment at import time. Callers that need a test-controlled set call
#: `default_shell_names` directly with their own mapping.
DEFAULT_SHELL_NAMES: frozenset[str] = default_shell_names(os.environ)


def classify(live_windows, entries, shell_names) -> StopPreview:
    """Classify every live window against the recorded entries, by
    exclusion against *shell_names* — never by a positive match on a
    conversation process name (see module docstring).

    A window whose recorded entry carries a conversation id and whose
    foreground command is not in *shell_names* is `live-conversation`; the
    same entry at a shell is `exited-conversation`. A window with no
    conversation id (no entry at all, or an entry with none) whose
    foreground command is not in *shell_names* is `foreground`; the rest
    are `idle`. Only tmux's live windows produce rows — a recorded entry
    whose window is no longer live is reconciliation's business, not the
    preview's.
    """
    entries_by_id = {entry.window_id: entry for entry in entries}
    rows = []
    for window in live_windows:
        entry = entries_by_id.get(window.window_id)
        has_conversation = entry is not None and entry.conversation_id is not None
        is_shell = window.current_command in shell_names
        if has_conversation:
            kind = "exited-conversation" if is_shell else "live-conversation"
            conversation_id = entry.conversation_id
        else:
            kind = "idle" if is_shell else "foreground"
            conversation_id = None
        rows.append(
            PreviewRow(
                window_id=window.window_id,
                name=window.name,
                conversation_id=conversation_id,
                kind=kind,
                command=window.current_command,
            )
        )
    return StopPreview(windows=tuple(rows))


#: The word each renderable member renders as, both in the human line and
#: as the JSON `outcome` field. Keyed by exact type, matched in
#: `_render_word`. A refusal has no entry: its message is composed by the
#: code that constructs it, exactly as `door.render_human` treats a refusal.
_OUTCOME_WORD: dict[type, str] = {
    Stopped: "stopped",
    NotRunning: "not-running",
    StillPresent: "still-present",
}


def _render_word(outcome: StopOutcome) -> str:
    word = _OUTCOME_WORD.get(type(outcome))
    if word is None:
        raise TypeError(
            f"{type(outcome).__name__} has no human/JSON rendering — only "
            "Stopped, NotRunning, and StillPresent render; a refusal's "
            "message is composed by the code that constructs it."
        )
    return word


def _row_line(row: PreviewRow) -> str:
    head = f'  {row.window_id} "{row.name}"'
    if row.kind == "live-conversation":
        return f"{head}  conversation {row.conversation_id}  live"
    if row.kind == "exited-conversation":
        return f"{head}  conversation {row.conversation_id}  exited"
    if row.kind == "foreground":
        return f"{head}  foreground: {row.command}"
    return head  # idle: a window at an idle shell shows neither


def preview_lines(tmux_session: str, preview: StopPreview) -> list[str]:
    """The count line, then one indented line per window — the body every
    rendering of a non-empty preview shares. When `preview.listed` is
    `False`, the single "could not list" line replaces the count line and
    rows entirely: "could not tell" must never print as "0 windows".

    Escaped whole through `printable_path`, line by line, the same way
    `render_human` escapes its own composed lines: see this module's
    docstring. Factored out so `stop_workspace` can hand these SAME lines to
    `emit` before the kill, without re-deriving the formatting a second
    time — see that function's docstring for why the preview is emitted
    before anything is killed. The engine reuses this same function for the
    unlisted case too, rather than composing its own line, so it and
    `render_human` can never disagree on the wording.
    """
    if not preview.listed:
        return [printable_path(f"camp: could not list the windows of {tmux_session}; stopping without a preview")]
    count = len(preview.windows)
    noun = "window" if count == 1 else "windows"
    lines = [printable_path(f"stopping {tmux_session}: {count} {noun}")]
    lines.extend(printable_path(_row_line(row)) for row in preview.windows)
    return lines


def outcome_line(outcome: StopOutcome) -> str:
    """The one line `camp stop` prints after the preview: `not running
    <session>` for `NotRunning`, `stopped <session>` for `Stopped`, or the
    still-present message naming the manual next step for `StillPresent`.

    Factored out of :func:`render_human` so a streaming caller — `cli/stop.py`,
    which has already emitted the preview lines through `emit` before the
    kill — can print exactly this line afterward instead of re-rendering the
    preview a second time. `render_human` itself still composes the full
    rendering (preview plus this line) for any non-streaming caller.
    """
    _render_word(outcome)  # raises for a refusal; message composed elsewhere

    if isinstance(outcome, NotRunning):
        return printable_path(f"not running {outcome.tmux_session}")
    if isinstance(outcome, Stopped):
        return printable_path(f"stopped {outcome.tmux_session}")
    # StillPresent
    return printable_path(
        f"camp stop: {outcome.tmux_session} is still present after kill-session; "
        f"run camp stop again, or kill it in tmux with tmux kill-session -t ={outcome.tmux_session}"
    )


def render_human(outcome: StopOutcome) -> str:
    """The human lines `camp stop` prints for *outcome*.

    Every line — the count line, each preview row, the outcome line — is
    escaped whole through `printable_path`, the same way `door.render_human`
    escapes its own composed line: see this module's docstring.
    """
    if isinstance(outcome, NotRunning):
        return outcome_line(outcome)

    lines = preview_lines(outcome.tmux_session, outcome.preview)
    lines.append(outcome_line(outcome))
    return "\n".join(lines)


def render_json(outcome: StopOutcome) -> dict:
    """The `--json` object for *outcome* (`Stopped`, `NotRunning`, or `StillPresent`)."""
    word = _render_word(outcome)
    preview = outcome.preview
    live_conversations = [r.conversation_id for r in preview.windows if r.kind == "live-conversation"]
    exited_conversations = [r.conversation_id for r in preview.windows if r.kind == "exited-conversation"]
    foreground = [r.name for r in preview.windows if r.kind == "foreground"]
    return {
        "ok": word in ("stopped", "not-running"),
        "outcome": word,
        "slug": outcome.slug,
        "group": outcome.group,
        "tmux_session": outcome.tmux_session,
        "windows": len(preview.windows) if preview.listed else None,
        "live_conversations": live_conversations,
        "exited_conversations": exited_conversations,
        "foreground": foreground,
        "reconciled": preview.reconciled,
        "listed": preview.listed,
    }


#: Exit status per exact member type. Looked up by `type(outcome)`, never by
#: `isinstance` or a permissive default, so a `StopOutcome` subclass added
#: without an entry here raises `KeyError` instead of silently reporting
#: success or failure it was never told to.
_EXIT_STATUS: dict[type, int] = {
    Stopped: 0,
    NotRunning: 0,
    StillPresent: 1,
    RefusedNoWorkspace: 1,
    RefusedTmuxUnanswered: 1,
}


def exit_status(outcome: StopOutcome) -> int:
    """The process exit status for *outcome*: 0 for stopped/not-running, 1 otherwise."""
    return _EXIT_STATUS[type(outcome)]


def stop_workspace(
    group: str,
    slug: str,
    workspace_dir: Path,
    *,
    tmux: Any,
    poll_timeout: float = POLL_TIMEOUT_SECONDS,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    emit: Callable[[str], None],
    shell_names: frozenset[str] = DEFAULT_SHELL_NAMES,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> StopOutcome:
    """`camp stop`'s engine: resolve the session name, reconcile, preview,
    kill, and check — see this module's docstring and
    `docs/design/the-record-stays-true-reconciliation-and-stopping.md`'s
    "Stopping states its cost, then pays it, then checks".

    In order: `has_session` decides `RefusedTmuxUnanswered` (tmux did not
    answer) or `NotRunning` (no session; the record is never touched — there
    is no tmux to reconcile it against) before anything else runs. A running
    session is reconciled under the workspace lock
    (`reconcile_workspace_record`) — its change lines, or a corrupt record's
    not-reconciled note, handed to *emit* one line at a time. A FRESH
    `list_windows` call — never the reconciliation's own internal listing —
    is then classified against the (possibly corrected) entries into a
    `StopPreview`, whose lines (`preview_lines`) are handed to *emit* too,
    before `tmux.kill_session` is ever called: a caller streaming *emit*
    straight to a terminal sees exactly what a kill is about to cost before
    it happens, matching the design doc's transcripts. Only then is the
    session killed and `poll_for_absence` (shared with `stop_session`, so
    `camp stop` and `camp kill` can never drift on how long an operator
    waits) polled to confirm it is gone.

    The record is written at most once, during reconciliation, and never
    again — a stop that ends in `StillPresent` or `RefusedTmuxUnanswered`
    (mid-poll) leaves the reconciled record exactly as reconciled, and the
    workspace is exactly as resurrectable as it was before the stop.

    A corrupt record cannot be classified against any entries — reconciling
    it returns none, so the preview is built as though camp knew nothing
    recorded, which is the truth: the kill still proceeds (see the design
    doc's "the stop proceeds, because the record was already lost before the
    stop was asked for").

    A listing that answers `None` or `UNANSWERED` at the (post-reconcile)
    preview step — the session raced away between the two tmux calls, a
    vanishingly narrow window — prints no count line, since "could not tell"
    is never read as "no windows", and says instead that the windows could
    not be listed. The stop still proceeds: the kill is of the whole session
    regardless of what is inside it, and a preview is a courtesy, not a
    precondition.
    """
    session_name = workspace_session_name(group, slug)

    present = tmux.has_session(session_name)
    if present is None:
        return RefusedTmuxUnanswered(slug=slug, group=group, tmux_session=session_name)
    if not present:
        return NotRunning(slug=slug, group=group, tmux_session=session_name)

    reconcile_outcome = reconcile_workspace_record(workspace_dir, session_name, tmux)
    for line in render_reconcile_lines(reconcile_outcome):
        emit(line)
    if isinstance(reconcile_outcome, Reconciled):
        entries = reconcile_outcome.entries
        reconciled = True
        reconcile_note = None
    else:
        entries = ()
        reconciled = False
        reconcile_note = reconcile_outcome.reason

    listing = tmux.list_windows(session_name)
    if isinstance(listing, WindowListing):
        preview = replace(
            classify(listing.windows, entries, shell_names),
            reconciled=reconciled,
            reconcile_note=reconcile_note,
        )
        for line in preview_lines(session_name, preview):
            emit(line)
    else:
        # "Could not tell" is never read as "no windows": no count line,
        # one line saying so, and the kill the operator asked for proceeds.
        preview = StopPreview(windows=(), reconciled=reconciled, reconcile_note=reconcile_note, listed=False)
        for line in preview_lines(session_name, preview):
            emit(line)

    tmux.kill_session(session_name)

    result = poll_for_absence(
        tmux,
        session_name,
        sleep=sleep,
        monotonic=monotonic,
        poll_timeout=poll_timeout,
        poll_interval=poll_interval,
    )
    if result is None:
        return RefusedTmuxUnanswered(slug=slug, group=group, tmux_session=session_name, preview=preview)
    if result:
        return Stopped(slug=slug, group=group, tmux_session=session_name, preview=preview)
    return StillPresent(slug=slug, group=group, tmux_session=session_name, preview=preview)
