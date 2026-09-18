"""The door's outcome — the closed value type `camp attach` reports through.

Mirrors :class:`~camp.launch.stop.StopOutcome`: a frozen dataclass base that
is never returned itself, with one member per outcome. Pure values only —
nothing here does I/O, prints, or touches tmux. The dispatch that decides
which member applies, and the CLI wiring that prints and exits, are later
tasks' work; this module only defines what can be said and how it renders.

Eight members. Two are the door's successes — :class:`Created` and
:class:`Connected` — carrying the fields a caller needs to report on and to
hand off the terminal with. The other six are refusals, one per way the door
declines to act: :class:`RefusedNoWorkspace`, :class:`RefusedNoTerminal`,
:class:`RefusedEmptyGroup`, :class:`RefusedTmuxUnanswered`,
:class:`RefusedCreateFailed`, :class:`RefusedCreateRefused`. See
``docs/design/the-door-creates-or-connects-a-workspace-session.md`` for the
state each corresponds to; the slug-resolution refusals are constructed by
the task that resolves a slug into a workspace or a refusal, and the tmux
refusals are constructed by the task that dispatches the create/connect
attempt.

Three renderings, all pure functions of a :class:`DoorOutcome`:

- :func:`render_human` — the one line `camp attach` prints on stdout for a
  success: ``created <tmux session>`` / ``connected <tmux session>``.
  Composed and escaped as a single unit through
  :func:`~camp.launch.recovery.printable_path`, never field by field, so a
  control character in the session name cannot inject a second line — see
  `lesson/escape-the-composed-output-line-not-a-chosen-subset-of-its-fields`.
- :func:`render_json` — the `--json` object for a success. `outcome` is an
  open vocabulary (`resurrected` joins it once the window record exists);
  `attached` varies independently of it, because a non-interactive
  invocation creates or connects a session and attaches nothing, and that is
  still success. `tmux_session` and `workspace_path` carry the same values,
  spelled the same way, that `camp list`'s rows already carry.
- :func:`exit_status` — `0` for created and connected, `1` for every
  refusal. No third code: AC16's resurrection arm has no producer until the
  window record exists, and a reserved number nothing can emit would be a
  contract item with nothing holding it honest.

Both renderings apply only to the two success members — a refusal's message
is composed by the task that constructs it, not read back out of this
module — so calling either on a refusal is a programming error, not a case
to degrade gracefully from.
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


#: The word each success member renders as, both in the human line and as
#: the JSON `outcome` field. Keyed by exact type, matched in `_render_word`.
_OUTCOME_WORD: dict[type, str] = {
    Created: "created",
    Connected: "connected",
}


def _render_word(outcome: DoorOutcome) -> str:
    word = _OUTCOME_WORD.get(type(outcome))
    if word is None:
        raise TypeError(
            f"{type(outcome).__name__} has no human/JSON rendering — "
            "only Created and Connected render; a refusal's message is "
            "composed by the code that constructs it."
        )
    return word


def render_human(outcome: DoorOutcome) -> str:
    """The one-line human rendering of a created or connected outcome.

    Escaped as a single composed string, never per field — see this
    module's docstring and
    `lesson/escape-the-composed-output-line-not-a-chosen-subset-of-its-fields`.
    """
    word = _render_word(outcome)
    return printable_path(f"{word} {outcome.tmux_session}")


def render_json(outcome: DoorOutcome) -> dict:
    """The `--json` object for a created or connected outcome."""
    word = _render_word(outcome)
    return {
        "ok": True,
        "outcome": word,
        "slug": outcome.slug,
        "group": outcome.group,
        "workspace_path": str(outcome.workspace_path),
        "tmux_session": outcome.tmux_session,
        "attached": outcome.attached,
    }


#: Exit status per exact member type. Looked up by `type(outcome)`, never by
#: `isinstance` or a permissive default, so a `DoorOutcome` subclass added
#: without an entry here raises `KeyError` instead of silently reporting
#: success or failure it was never told to.
_EXIT_STATUS: dict[type, int] = {
    Created: 0,
    Connected: 0,
    RefusedNoWorkspace: 1,
    RefusedNoTerminal: 1,
    RefusedEmptyGroup: 1,
    RefusedTmuxUnanswered: 1,
    RefusedCreateFailed: 1,
    RefusedCreateRefused: 1,
}


def exit_status(outcome: DoorOutcome) -> int:
    """The process exit status for *outcome*: 0 for created/connected, 1 for every refusal."""
    return _EXIT_STATUS[type(outcome)]


#: The machine-readable word for the two tmux-boundary refusals that share
#: `_refuse_door`'s JSON shape (`cli/session.py`) — `None` for a refusal with
#: no defined word, so a caller can omit the key rather than print `None`.
#: `RefusedCreateFailed` and `RefusedCreateRefused` must render distinct
#: words: a `--json` consumer needs to tell a policy refusal from a
#: transient tmux failure without parsing the free-text `reason`.
_REFUSAL_WORD: dict[type, str] = {
    RefusedTmuxUnanswered: "tmux_unanswered",
    RefusedCreateFailed: "create_failed",
    RefusedCreateRefused: "create_refused",
}


def refusal_outcome_word(outcome: DoorOutcome) -> str | None:
    """The `outcome` word for a door refusal, or `None` if this refusal type
    defines none (the slug-resolution refusals, reported through a different
    path with no shared JSON shape to carry it)."""
    return _REFUSAL_WORD.get(type(outcome))
