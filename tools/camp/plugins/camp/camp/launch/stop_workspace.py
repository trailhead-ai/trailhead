"""The stop outcome — the closed value type `camp stop` reports through, and
the pure classification that builds its preview.

Mirrors :class:`~camp.launch.door.DoorOutcome`: a frozen dataclass base that
is never returned itself, with one member per outcome. Pure values only —
nothing here does I/O, prints, or touches tmux. The engine that resolves a
slug, reconciles the record, kills the session, and constructs one of these
members is `stop_workspace` (a later task); this module only defines what
can be said, how the preview is classified, and how the outcome renders.

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
  per non-idle window — before their own outcome line; `NotRunning` prints
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
from dataclasses import dataclass, field
from typing import Mapping

from .recovery import printable_path


@dataclass(frozen=True)
class PreviewRow:
    """One live tmux window, classified for the stop preview.

    `conversation_id` is set only for the two conversation kinds; a
    `foreground` or `idle` row carries `None`, since neither corresponds to
    a recorded conversation. `kind` is one of `live-conversation`,
    `exited-conversation`, `foreground`, `idle`.
    """

    window_id: str
    name: str
    conversation_id: str | None
    kind: str


@dataclass(frozen=True)
class StopPreview:
    """What a stop is about to cost: every live window, classified.

    `reconciled` and `reconcile_note` are not set by `classify` — they
    describe whether the *record* was corrected against tmux before the
    preview was built, which `classify` has no way to know from a window
    list alone. The engine that reconciles then classifies sets them via
    `dataclasses.replace`.
    """

    windows: tuple[PreviewRow, ...] = ()
    reconciled: bool = False
    reconcile_note: str | None = None


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


def _row_line(row: PreviewRow) -> str | None:
    if row.kind == "live-conversation":
        return f'  {row.window_id} "{row.name}"  conversation {row.conversation_id}  live'
    if row.kind == "exited-conversation":
        return f'  {row.window_id} "{row.name}"  conversation {row.conversation_id}  exited'
    if row.kind == "foreground":
        return f'  {row.window_id} "{row.name}"  foreground'
    return None  # idle: a window at an idle shell shows neither


def render_human(outcome: StopOutcome) -> str:
    """The human lines `camp stop` prints for *outcome*.

    Every line — the count line, each preview row, the outcome line — is
    escaped whole through `printable_path`, the same way `door.render_human`
    escapes its own composed line: see this module's docstring.
    """
    _render_word(outcome)  # raises for a refusal; message composed elsewhere

    if isinstance(outcome, NotRunning):
        return printable_path(f"not running {outcome.tmux_session}")

    preview = outcome.preview
    lines = [printable_path(f"stopping {outcome.tmux_session}: {len(preview.windows)} windows")]
    for row in preview.windows:
        line = _row_line(row)
        if line is not None:
            lines.append(printable_path(line))

    if isinstance(outcome, Stopped):
        lines.append(printable_path(f"stopped {outcome.tmux_session}"))
    else:  # StillPresent
        lines.append(
            printable_path(
                f"camp stop: {outcome.tmux_session} is still present after kill-session; "
                f"run camp stop again, or kill it in tmux with tmux kill-session -t ={outcome.tmux_session}"
            )
        )
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
        "windows": len(preview.windows),
        "live_conversations": live_conversations,
        "exited_conversations": exited_conversations,
        "foreground": foreground,
        "reconciled": preview.reconciled,
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
