"""The door's outcome — the closed value type `camp attach` reports through.

Mirrors :class:`~camp.launch.stop.StopOutcome`: a frozen dataclass base that
is never returned itself, with one member per outcome. Pure values only —
nothing here does I/O, prints, or touches tmux. The dispatch that decides
which member applies, and the CLI wiring that prints and exits, are later
tasks' work; this module only defines what can be said and how it renders.

Ten members. Three are the door's successes — :class:`Created`,
:class:`Connected`, and :class:`Resurrected` — carrying the fields a caller
needs to report on and to hand off the terminal with. The other seven are
refusals, one per way the door declines to act: :class:`RefusedNoWorkspace`,
:class:`RefusedNoTerminal`, :class:`RefusedEmptyGroup`,
:class:`RefusedTmuxUnanswered`, :class:`RefusedCreateFailed`,
:class:`RefusedCreateRefused`, :class:`RefusedRecordUnreadable`. See
``docs/design/the-door-creates-or-connects-a-workspace-session.md`` and
``docs/design/the-door-resurrects-a-workspace-from-its-record.md`` for the
state each corresponds to; the slug-resolution refusals are constructed by
the task that resolves a slug into a workspace or a refusal, and the tmux
refusals are constructed by the task that dispatches the create/connect
attempt.

Three renderings, all pure functions of a :class:`DoorOutcome`:

- :func:`render_human` — the one line `camp attach` prints on stdout for a
  success: ``created <tmux session>`` / ``connected <tmux session>`` /
  ``resurrected <tmux session> (<n> windows)`` (or its partial/dropped
  forms — see :class:`Resurrected`). Composed and escaped as a single unit
  through :func:`~camp.launch.recovery.printable_path`, never field by
  field, so a control character in the session name cannot inject a second
  line — see
  `lesson/escape-the-composed-output-line-not-a-chosen-subset-of-its-fields`.
- :func:`render_json` — the `--json` object for a success. `outcome` is one
  of `created`, `connected`, `resurrected`; `attached` varies independently
  of it, because a non-interactive invocation creates, connects, or
  resurrects a session and attaches nothing, and that is still success.
  `tmux_session` and `workspace_path` carry the same values, spelled the
  same way, that `camp list`'s rows already carry. `windows` is present
  only on `Resurrected` — `Created` and `Connected` have no windows to
  account for.
- :func:`exit_status` — `0` for created and connected, `1` for every
  refusal, and for `Resurrected` specifically: `0` when nothing failed to
  come back, `2` when at least one window did — AC16's third exit code.
  `_EXIT_STATUS` stays keyed by exact type; only the `Resurrected` entry's
  *value* is a function of the outcome instance rather than a constant.

Both renderings apply only to the three success members — a refusal's
message is composed by the task that constructs it, not read back out of
this module — so calling either on a refusal is a programming error, not a
case to degrade gracefully from.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .recovery import printable_path


@dataclass(frozen=True)
class DoorOutcome:
    """Base of the closed set of outcomes the door can report. Never returned itself."""


@dataclass(frozen=True)
class Created(DoorOutcome):
    """No session existed; one was created and the report reflects that."""

    slug: str
    group: str
    tmux_session: str
    workspace_path: Path
    attached: bool


@dataclass(frozen=True)
class Connected(DoorOutcome):
    """A session already existed; the door reports joining it, not creating it."""

    slug: str
    group: str
    tmux_session: str
    workspace_path: Path
    attached: bool


@dataclass(frozen=True)
class Resurrected(DoorOutcome):
    """The window record held entries and the session came back from them —
    `restored`/`failed`/`dropped` are counts, not the full
    `~camp.launch.resurrect.ResurrectionResult`: this module renders, it
    does not carry engine state. `failed` decides the exit status (see
    :func:`exit_status`); `dropped` is reported in the human line only when
    nothing failed, and is always available to a `--json` reader via
    `render_json`'s `windows` key regardless."""

    slug: str
    group: str
    tmux_session: str
    workspace_path: Path
    attached: bool
    restored: int
    failed: int
    dropped: int


@dataclass(frozen=True)
class RefusedNoWorkspace(DoorOutcome):
    """The given slug names no workspace in the resolved group."""


@dataclass(frozen=True)
class RefusedNoTerminal(DoorOutcome):
    """No slug was given and there is no interactive terminal to prompt into."""


@dataclass(frozen=True)
class RefusedEmptyGroup(DoorOutcome):
    """No slug was given, a terminal is available, and the group has no workspaces to offer."""


@dataclass(frozen=True)
class RefusedTmuxUnanswered(DoorOutcome):
    """tmux did not answer, so the door cannot tell whether a session exists."""


@dataclass(frozen=True)
class RefusedCreateFailed(DoorOutcome):
    """tmux answered, a create was attempted, and it failed for a transient
    reason — an unreachable tmux binary, a timed-out server bring-up. Distinct
    from :class:`RefusedCreateRefused`, which is a policy refusal, not tmux
    having a bad moment."""


@dataclass(frozen=True)
class RefusedCreateRefused(DoorOutcome):
    """tmux was never asked: the workspace directory is at, under, or above a
    credential store, and the create was refused by policy before any tmux
    call. Distinct from :class:`RefusedCreateFailed`, a transient failure."""


@dataclass(frozen=True)
class RefusedRecordUnreadable(DoorOutcome):
    """The window record exists but could not be parsed — no tmux call is
    ever made for this refusal. A session with no windows would present a
    workspace camp cannot describe as an empty one; the remedy is in the
    reason the caller composes: fix or remove the record and retry."""


#: The word each success member renders as, both in the human line and as
#: the JSON `outcome` field. Keyed by exact type, matched in `_render_word`.
_OUTCOME_WORD: dict[type, str] = {
    Created: "created",
    Connected: "connected",
    Resurrected: "resurrected",
}


def _render_word(outcome: DoorOutcome) -> str:
    word = _OUTCOME_WORD.get(type(outcome))
    if word is None:
        raise TypeError(
            f"{type(outcome).__name__} has no human/JSON rendering — "
            "only Created, Connected, and Resurrected render; a refusal's "
            "message is composed by the code that constructs it."
        )
    return word


def _resurrected_windows_phrase(outcome: "Resurrected") -> str:
    """The parenthesized window-count phrase `Resurrected`'s human line
    ends with — varies with `failed` and `dropped`. `failed` takes
    precedence over `dropped` when both are non-zero: a caller that cares
    about a dropped count reads `windows.dropped` (always present under
    `--json`); the drop itself is also already reported on its own stderr
    line by `render_resurrection_lines`, so the one-line summary's job here
    is to say what tmux did, not to repeat every detail.

    `window` singularizes when `restored == 1` in the whole and dropped
    forms; the failed branch's total always stays plural (`1 of 3 windows`)
    — it counts every window attempted, not just the ones that came back."""
    total_attempted = outcome.restored + outcome.failed
    if outcome.failed:
        return f"({outcome.restored} of {total_attempted} windows; {outcome.failed} did not come back)"
    word = "window" if outcome.restored == 1 else "windows"
    if outcome.dropped:
        return f"({outcome.restored} {word}; {outcome.dropped} dropped)"
    return f"({outcome.restored} {word})"


def render_human(outcome: DoorOutcome) -> str:
    """The one-line human rendering of a created, connected, or resurrected
    outcome.

    Escaped as a single composed string, never per field — see this
    module's docstring and
    `lesson/escape-the-composed-output-line-not-a-chosen-subset-of-its-fields`.
    """
    word = _render_word(outcome)
    if isinstance(outcome, Resurrected):
        phrase = _resurrected_windows_phrase(outcome)
        return printable_path(f"{word} {outcome.tmux_session} {phrase}")
    return printable_path(f"{word} {outcome.tmux_session}")


def render_json(outcome: DoorOutcome) -> dict:
    """The `--json` object for a created, connected, or resurrected outcome.

    `windows` is present only for `Resurrected` — `Created` and `Connected`
    have no windows to account for.
    """
    word = _render_word(outcome)
    payload = {
        "ok": True,
        "outcome": word,
        "slug": outcome.slug,
        "group": outcome.group,
        "workspace_path": str(outcome.workspace_path),
        "tmux_session": outcome.tmux_session,
        "attached": outcome.attached,
    }
    if isinstance(outcome, Resurrected):
        payload["windows"] = {
            "restored": outcome.restored,
            "failed": outcome.failed,
            "dropped": outcome.dropped,
        }
    return payload


#: Exit status per exact member type. Looked up by `type(outcome)`, never by
#: `isinstance` or a permissive default, so a `DoorOutcome` subclass added
#: without an entry here raises `KeyError` instead of silently reporting
#: success or failure it was never told to. `Resurrected`'s entry is a
#: function of the outcome instance (`failed == 0`), not a constant — see
#: `exit_status`.
_EXIT_STATUS: dict[type, int] = {
    Created: 0,
    Connected: 0,
    Resurrected: 0,
    RefusedNoWorkspace: 1,
    RefusedNoTerminal: 1,
    RefusedEmptyGroup: 1,
    RefusedTmuxUnanswered: 1,
    RefusedCreateFailed: 1,
    RefusedCreateRefused: 1,
    RefusedRecordUnreadable: 1,
}


def exit_status(outcome: DoorOutcome) -> int:
    """The process exit status for *outcome*.

    0 for created/connected, 1 for every refusal, and for `Resurrected`
    specifically: 0 when `failed == 0`, 2 when at least one window did not
    come back — AC16's third exit code.
    """
    if isinstance(outcome, Resurrected):
        return 0 if outcome.failed == 0 else 2
    return _EXIT_STATUS[type(outcome)]


#: The machine-readable word for the tmux-boundary refusals that share
#: `_refuse_door`'s JSON shape (`cli/session.py`) — `None` for a refusal with
#: no defined word, so a caller can omit the key rather than print `None`.
#: `RefusedCreateFailed` and `RefusedCreateRefused` must render distinct
#: words: a `--json` consumer needs to tell a policy refusal from a
#: transient tmux failure without parsing the free-text `reason`.
_REFUSAL_WORD: dict[type, str] = {
    RefusedTmuxUnanswered: "tmux_unanswered",
    RefusedCreateFailed: "create_failed",
    RefusedCreateRefused: "create_refused",
    RefusedRecordUnreadable: "record_unreadable",
}


def refusal_outcome_word(outcome: DoorOutcome) -> str | None:
    """The `outcome` word for a door refusal, or `None` if this refusal type
    defines none (the slug-resolution refusals, reported through a different
    path with no shared JSON shape to carry it)."""
    return _REFUSAL_WORD.get(type(outcome))
