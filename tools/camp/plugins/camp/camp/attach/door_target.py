"""Resolves what `camp attach` is being pointed at — a workspace, a picker
choice among workspaces, or a refusal — before anything acts on the answer.

Pure resolution: nothing here creates a tmux session, attaches to one, or
reads tmux at all. Four inputs decide the answer — the argument (a slug or
nothing), whether stdin and stdout are both terminals, the group's
workspaces (read lazily, see below), and the operator's choice when the
picker runs. See ``docs/design/the-door-creates-or-connects-a-workspace-
session.md``'s "Enumerated states" section, the "Slug resolution:" half.

Precedence, not pattern. A slug that names a workspace in the resolved
group resolves to that workspace outright (:class:`ResolvedWorkspace`). A
slug that names no workspace is NOT a refusal this module owns — it falls
through to the retired ref path unchanged, represented here by
:class:`NotAWorkspace`, exactly as `docs/design/the-door-creates-or-
connects-a-workspace-session.md`'s "A slug naming no workspace in the
group" state describes: "camp creates no workspace here … it falls through
to the retired ref resolver, which refuses in its own words."

*workspaces* is a zero-argument callable, not a list, so the terminal check
for the bare (no-slug) form can run BEFORE anything enumerates the group —
the design doc's "Check the terminal before enumerating workspaces, so the
no-terminal refusal reads the same in an empty group as in a full one." A
slug-given call never even calls *workspaces* lazily-for-terminal reasons
(a slug always needs the list to check against), but the bare form calls it
only after the terminal is confirmed present.

Refusal mapping, settled here rather than left to the next task. Slug
resolution reaches exactly two of `camp.launch.door`'s six refusal members
— :class:`~camp.launch.door.RefusedNoTerminal` and
:class:`~camp.launch.door.RefusedEmptyGroup` — constructed from that module
rather than a second vocabulary defined here, per that module's own
docstring ("the slug-resolution refusals are constructed by the task that
resolves a slug into a workspace or a refusal"). The design doc's "No slug
given and no interactive terminal" state is a single combined state (no
slug AND no terminal never occur apart — a slug given short-circuits before
the terminal is ever consulted, and a slug absent WITH a terminal goes to
the picker, never a refusal for the terminal alone), and its own text calls
this "the no-terminal refusal" verbatim — so it maps to
``RefusedNoTerminal``. ``RefusedNoWorkspace`` is UNREACHABLE from this
module: its docstring ("The given slug names no workspace in the resolved
group") reads as a perfect fit for the fall-through state, but the design
doc is explicit that state falls through to the retired ref path instead —
"Once the retirement slice removes the ref path, this refusal becomes the
door's own" — so it belongs to that later slice, not this one.

The picker's re-prompt loop is `camp.attach.picker`'s own
(:func:`~camp.attach.picker._prompt_for_index`), generalized there and
reused here rather than duplicated — see that function's docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import IO, Callable, Sequence

from ..launch.door import DoorOutcome, RefusedEmptyGroup, RefusedNoTerminal
from .picker import PoolUnreadable, _prompt_for_index


@dataclass(frozen=True)
class WorkspaceCandidate:
    """One workspace this module's picker can present, or match a slug
    against. ``state_text`` is already rendered through
    :func:`~camp.launch.inventory.format_state` by the caller that builds
    these — the same field `camp list` prints, from the same classifier —
    so this module never re-derives it."""

    slug: str
    path: Path
    state_text: str


@dataclass(frozen=True)
class ResolvedWorkspace:
    """A slug, or a picker choice, resolved to this workspace in this
    group. Not a :class:`~camp.launch.door.DoorOutcome` — the door acts on
    this value; this module only produces it."""

    group: str
    slug: str
    path: Path


@dataclass(frozen=True)
class NotAWorkspace:
    """The given slug names no workspace in the resolved group.

    Not a refusal of the door's — the caller falls through to the retired
    ref path unchanged, which refuses in its own words. See this module's
    docstring for why this is deliberately not
    :class:`~camp.launch.door.RefusedNoWorkspace`.
    """


#: What :func:`resolve_attach_target` can answer with.
AttachTargetResult = (
    ResolvedWorkspace | NotAWorkspace | RefusedNoTerminal | RefusedEmptyGroup | PoolUnreadable
)


def resolve_attach_target(
    ref: str | None,
    *,
    group_name: str,
    workspaces: Callable[[], Sequence[WorkspaceCandidate]],
    isatty: bool,
    stdin: IO[str],
    stdout: IO[str],
) -> AttachTargetResult:
    """Resolve *ref* (a slug, or ``None`` for the bare form) against the
    workspaces *workspaces* enumerates — called at most once, and never
    before it has to be (see the module docstring's terminal-before-
    enumeration ordering).

    A slug is checked against every candidate *workspaces()* yields;
    matching one returns :class:`ResolvedWorkspace`, matching none returns
    :class:`NotAWorkspace`. With no slug, an absent terminal refuses
    without ever calling *workspaces*; a present terminal enumerates, and
    an empty result refuses while a non-empty one drives the numbered
    picker (reusing `camp.attach.picker`'s own re-prompt loop) through to a
    choice.
    """
    if ref is not None:
        for candidate in workspaces():
            if candidate.slug == ref:
                return ResolvedWorkspace(group=group_name, slug=ref, path=candidate.path)
        return NotAWorkspace()

    if not isatty:
        return RefusedNoTerminal()

    rows = list(workspaces())
    if not rows:
        return RefusedEmptyGroup()

    def render() -> None:
        stdout.write("Workspaces:\n")
        for index, row in enumerate(rows, start=1):
            stdout.write(f"  {index}) {row.slug}  {row.state_text}\n")

    picked = _prompt_for_index(len(rows), stdin=stdin, stdout=stdout, render=render)
    if picked is None:
        return PoolUnreadable(reason="no input read — end of input reached")

    chosen = rows[picked]
    return ResolvedWorkspace(group=group_name, slug=chosen.slug, path=chosen.path)


def refusal_message(outcome: DoorOutcome | PoolUnreadable, *, group_name: str) -> str:
    """The `camp attach: …` line for a slug-resolution refusal.

    Composed here, not read back out of `camp.launch.door` — that module's
    own docstring is explicit that a refusal's message is composed by the
    code that constructs the refusal, never by the outcome type itself.
    """
    if isinstance(outcome, RefusedNoTerminal):
        return "camp attach: no workspace named — pass a slug"
    if isinstance(outcome, RefusedEmptyGroup):
        return (
            f'camp attach: no workspaces in group "{group_name}" — '
            "`camp new <slug>` creates one"
        )
    if isinstance(outcome, PoolUnreadable):
        return f"camp attach: {outcome.reason}"
    raise TypeError(f"{type(outcome).__name__} has no slug-resolution refusal message")
