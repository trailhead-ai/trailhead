"""The derived-name rule and the prefix resolver for addressing camp sessions.

This module is PURE: every function here maps data to data. It resolves paths,
reads group configs it is handed, and stats a root to see whether it still
exists — and nothing else. It starts no process, writes no file, prints nothing,
and never exits. Rendering, exit codes, and refusal wording belong to camp's CLI
layer; a question answered here must be answerable identically from a test, a
listing, and a launch, which it cannot be if the answer is a message on a
terminal. `tools/camp/tests/test_launch_recovery.py` asserts that boundary over
this file's own AST rather than trusting this paragraph.

ONE NAME RULE, THREE CALLERS. A session's name component — the middle field of
the tmux session name `camp-<component>-<uuid8>` — is derived from the directory
the session is rooted at, by :func:`derive_name_component`, and by nothing else.
A launch derives it to name the tmux session; a listing derives it to show the
operator an addressable name for a session camp did not launch and keeps no
record of; and an eligibility check derives it (via :func:`is_workspace_root`)
to tell a camp-managed workspace from a directory the operator named. Those
three must agree by construction, because the tmux name is what makes a
duplicate launch collide instead of silently doubling up: two answers to "what
is this session called" are two names, and two names never collide.

The rule is a RESOLVED-PATH TEST, and nothing more. A cwd equal to or under
`central_state_dir(<group>)/worktrees/<slug>`, for any group in the configs
handed in, yields that `<slug>`; anything else yields the cwd's own basename. No
manifest is consulted and no directory need exist: resolution is NON-STRICT
precisely so a session whose root has since been torn down still derives the
same name it had while it lived — a listing that cannot name a dead session's
root cannot offer it for recovery either. Both sides of the comparison are fully
resolved, so a symlink cannot make a workspace look like an unrelated directory
(or the reverse).

ADDRESSING IS BY PREFIX, OVER A UNION. :func:`resolve_session_ref` matches an
operator's ref against the union — keyed by session id — of the harness's
on-disk transcripts and its live records. The union is what keeps a live session
with no transcript on disk from reading as "no such session"; a caller that
looked only at transcripts would answer "not found" for a session running right
now, which is the worst available answer.

A ref matches when it is a PREFIX of a candidate's session id or of its derived
name. Prefix, never substring: a substring match makes the set of things an
operator's ref can hit unpredictable, and this ref chooses which session gets
resumed. A harness's own display name for a session is never matched against —
it is the harness's string, not camp's, and camp cannot guarantee it is unique
or stable.

THE RECOVERABLE LISTING IS A SUBTRACTION. :func:`recoverable_candidates` maps
the same two pools to the sessions that are DEAD — enumerated transcripts minus
the live set — and takes both pools already scoped by the caller. It scopes
nothing itself, because scoping belongs to the seam each pool came from and
this module cannot tell a scoped pool from an unscoped one; the failure that
guards against is a live session listed as recoverable.

The outcome is one of exactly three shapes — :class:`Resolved`,
:class:`Ambiguous`, :class:`NoMatch` — and a caller is expected to handle all
three. :class:`NoMatch` carries the size of the pool it searched because "there
are no sessions at all" and "there are sessions, none matching" call for
different answers to the operator, and only this function knows which one
happened.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..group.resolve import central_state_dir


def _workspace_containers(
    groups: Iterable[dict[str, Any]], env: Mapping[str, str]
) -> tuple[Path, ...]:
    """The resolved `worktrees` directory of every group config handed in.

    Computed once per question and passed down, so naming a whole listing of
    sessions resolves each group's state directory once rather than once per
    row.

    A group whose config carries no name is skipped rather than raising: the
    name rule's contract is to answer, and one malformed entry must not be able
    to stop a listing from naming every other session.
    """
    containers = []
    for group in groups:
        name = (group.get("group") or {}).get("name")
        if not name:
            continue
        containers.append((central_state_dir(name, env=dict(env)) / "worktrees").resolve())
    return tuple(containers)


def _slug_at_or_under(resolved: Path, containers: Iterable[Path]) -> str | None:
    """The workspace slug *resolved* sits at or under, or ``None``.

    *resolved* and *containers* are both already fully resolved. The `worktrees`
    container itself is not a workspace — there is no slug there — so it answers
    ``None``.
    """
    for container in containers:
        try:
            relative = resolved.relative_to(container)
        except ValueError:
            continue
        if relative.parts:
            return relative.parts[0]
    return None


def _name_component(resolved: Path, containers: Iterable[Path]) -> str:
    """The name rule itself, over an already-resolved path: slug, else basename."""
    slug = _slug_at_or_under(resolved, containers)
    return slug if slug is not None else resolved.name


#: Characters a tmux session name may carry and still be addressable. tmux
#: reads ``:`` as the session/window separator and ``.`` as the window/pane
#: separator in a target, so a name containing either is created happily and
#: then cannot be named again — ``kill-session -t`` answers "can't find pane",
#: and the ``=`` exact-match prefix does not rescue it. Directory basenames
#: routinely carry dots, so this is an ordinary input, not a hostile one.
_NAME_COMPONENT_SAFE = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)

#: What an empty or fully-substituted component becomes, so a name is never
#: built with an empty middle.
_NAME_COMPONENT_FALLBACK = "dir"


def sanitize_name_component(raw: str) -> str:
    """Fold *raw* to the characters a tmux session name can be addressed by.

    Applied to every flavor's name component at the one place each is derived,
    so the name camp prints as an attach handle is a name tmux will accept back.
    Substitution rather than rejection: a directory is not invalid for being
    called ``my.project``, and refusing to launch there would be a worse answer
    than launching under a name that works.

    Not reversible, and not meant to be — the session id is the identity, and
    the name is an operator-facing handle.
    """
    folded = "".join(c if c in _NAME_COMPONENT_SAFE else "-" for c in raw)
    return folded.strip("-") or _NAME_COMPONENT_FALLBACK


def derive_name_component(
    cwd: Path | str,
    groups: Iterable[dict[str, Any]],
    *,
    env: Mapping[str, str],
) -> str:
    """The name component for a session rooted at *cwd*: a slug, or a basename.

    *groups* is the loaded group configs to test against; *env* is the
    environment the paths resolve under, and is required rather than defaulted
    so no caller can accidentally derive a name from the wrong machine's state
    directory.

    Answers for any path, existing or not. The result is folded to the
    characters a tmux session name can be addressed by (see
    :func:`sanitize_name_component`), so a path that resolves to the filesystem
    root — or whose basename is entirely separators — still yields a usable
    component rather than an empty one.
    """
    return sanitize_name_component(
        _name_component(Path(cwd).resolve(), _workspace_containers(groups, env))
    )


def is_workspace_root(
    cwd: Path | str,
    groups: Iterable[dict[str, Any]],
    *,
    env: Mapping[str, str],
) -> bool:
    """Is *cwd* inside a camp-managed workspace? The boolean half of the rule.

    True for the workspace directory itself AND for anything under it, because
    a group's `[harness] cwd` routinely roots a launch below the workspace (for
    example at a member repo) and that directory is every bit as camp-managed
    as its parent. This is what decides whether a directory needs to clear the
    operator-named-directory eligibility gate: a directory camp itself created
    from its own manifest is already fenced by construction.
    """
    containers = _workspace_containers(groups, env)
    return _slug_at_or_under(Path(cwd).resolve(), containers) is not None


@dataclass(frozen=True)
class SessionCandidate:
    """One addressable session, from a transcript, a live record, or both.

    ``derived_name`` is the tmux-shaped name `camp-<component>-<uuid8>`, and is
    the second thing a ref is matched against. Its component is folded by
    :func:`sanitize_name_component`, so the name a listing offers is the name the
    launch engine composes and tmux accepts back — a candidate named by a string
    tmux cannot address is a candidate nobody can attach, kill, or resume. When ``unreadable``, there is no
    component to put in it and it degrades to `camp-<uuid8>` — a name that is
    still addressable and still true, rather than a guessed location.

    ``root`` is where the session was started, taken from the transcript, or
    from the live record when there is no transcript. ``None`` means the harness
    could not tell camp where the session ran, which is exactly ``unreadable``;
    such a session must never be reported as being anywhere in particular.

    ``root_missing`` is True when there IS a root and it no longer exists on
    disk. It is deliberately not conflated with ``unreadable``: a torn-down root
    is a location camp knows and can name in a refusal, an unreadable one is
    not. A missing root is not a reason to hide the row — a session the operator
    cannot resume is still one they may want to know about.

    ``age_seconds`` is the time since the transcript was last written, and is
    ``None`` for a candidate that has no transcript yet (a session that started
    so recently, or whose harness stores transcripts so lazily, that only the
    live enumeration knows about it). It is never derived from a live record's
    start time, which measures something else.
    """

    session_id: str
    derived_name: str
    root: Path | None
    age_seconds: float | None
    live: bool
    root_missing: bool
    unreadable: bool


@dataclass(frozen=True)
class Resolution:
    """Base of the closed set of resolver outcomes. Never returned itself."""


@dataclass(frozen=True)
class Resolved(Resolution):
    """The ref addressed exactly one session."""

    candidate: SessionCandidate


@dataclass(frozen=True)
class Ambiguous(Resolution):
    """The ref addressed more than one session; camp will not choose between them."""

    candidates: tuple[SessionCandidate, ...]


@dataclass(frozen=True)
class NoMatch(Resolution):
    """The ref addressed nothing in a pool of *pool_size* sessions.

    ``pool_size == 0`` and ``pool_size > 0`` are different situations for the
    operator — an empty store versus a mistyped ref — and a caller is expected
    to say something different about each.
    """

    pool_size: int


def _build_candidate(
    session_id: str,
    transcript,
    record,
    *,
    containers: Sequence[Path],
    now: datetime,
) -> SessionCandidate:
    root = transcript.cwd if transcript is not None and transcript.cwd is not None else None
    if root is None and record is not None:
        root = record.cwd

    if root is None:
        derived_name = f"camp-{session_id[:8]}"
    else:
        component = sanitize_name_component(_name_component(root.resolve(), containers))
        derived_name = f"camp-{component}-{session_id[:8]}"

    age_seconds = None
    if transcript is not None:
        age_seconds = (now - transcript.modified_at).total_seconds()

    return SessionCandidate(
        session_id=session_id,
        derived_name=derived_name,
        root=root,
        age_seconds=age_seconds,
        live=record is not None,
        root_missing=root is not None and not root.exists(),
        unreadable=root is None,
    )


def recoverable_candidates(
    *,
    transcripts: Iterable[Any],
    live_records: Iterable[Any],
    groups: Iterable[dict[str, Any]],
    env: Mapping[str, str],
    now: datetime | None = None,
) -> tuple[SessionCandidate, ...]:
    """The DEAD sessions: enumerated *transcripts* minus the live *live_records*.

    The subtraction is keyed by session id and by nothing else. A live session
    is not recoverable — there is nothing to bring back up — so it is removed
    rather than marked, and a live record with no transcript subtracts nothing
    because it was never in the enumerated pool to begin with.

    BOTH POOLS MUST ALREADY BE SCOPED THE SAME WAY. This function cannot tell a
    pool that was scoped from one that was not, so a caller that scopes the
    transcripts and not the live records gets live sessions reported as dead —
    the one failure this listing must never produce. Scoping is the caller's
    job precisely because it belongs to the seam that gathered each pool.

    Ordering is NEWEST FIRST by transcript mtime, with the session id ascending
    as the tiebreak, so two transcripts written in the same instant still list
    in a fixed order. A session id appearing more than once — two harnesses
    reading the same store — yields exactly one row.

    *now* is injectable so a row's age is a function of its inputs, and *env* is
    required so a name is never derived from the wrong machine's state
    directory.
    """
    now = now if now is not None else datetime.now(timezone.utc)
    containers = _workspace_containers(groups, env)
    live_ids = {record.session_id for record in live_records}

    dead: dict[str, Any] = {}
    for transcript in transcripts:
        if transcript.session_id in live_ids:
            continue
        dead.setdefault(transcript.session_id, transcript)

    candidates = [
        _build_candidate(session_id, transcript, None, containers=containers, now=now)
        for session_id, transcript in dead.items()
    ]
    candidates.sort(key=lambda c: (c.age_seconds, c.session_id))
    return tuple(candidates)


def resolve_session_ref(
    ref: str,
    *,
    transcripts: Iterable[Any],
    live_records: Iterable[Any],
    groups: Iterable[dict[str, Any]],
    env: Mapping[str, str],
    now: datetime | None = None,
) -> Resolution:
    """Resolve an operator's *ref* against transcripts ∪ live records.

    *transcripts* are the harness's on-disk session transcripts and
    *live_records* its currently-running sessions; a session id present in both
    yields exactly ONE candidate, marked live. Neither may be ``None``: an
    unanswerable seam is a refusal the caller owns, not an empty pool this
    function may quietly assume.

    *now* is injectable so a candidate's age is a function of its inputs.

    An EMPTY ref never resolves. It is a prefix of everything, so it addresses
    nothing in particular, and completing it into "the only session that
    happens to exist right now" would make the same command mean different
    things on different days.
    """
    now = now if now is not None else datetime.now(timezone.utc)
    containers = _workspace_containers(groups, env)

    merged: dict[str, tuple[Any, Any]] = {}
    for transcript in transcripts:
        merged[transcript.session_id] = (transcript, None)
    for record in live_records:
        transcript, _ = merged.get(record.session_id, (None, None))
        merged[record.session_id] = (transcript, record)

    candidates = [
        _build_candidate(session_id, transcript, record, containers=containers, now=now)
        for session_id, (transcript, record) in merged.items()
    ]
    # Freshest first, with the age-less (live-only) candidates after them, so a
    # caller listing candidates gets a stable order it did not have to impose.
    candidates.sort(key=lambda c: (c.age_seconds is None, c.age_seconds or 0.0, c.session_id))

    matches = [
        candidate
        for candidate in candidates
        if candidate.session_id.startswith(ref) or candidate.derived_name.startswith(ref)
    ]

    if not matches:
        return NoMatch(pool_size=len(candidates))
    if len(matches) == 1 and ref:
        return Resolved(candidate=matches[0])
    return Ambiguous(candidates=tuple(matches))
