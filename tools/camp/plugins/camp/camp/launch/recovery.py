"""The derived-name rule and the addressable-session pool for camp sessions.

This module is PURE: every function here maps data to data. It resolves paths,
reads group configs it is handed, and stats a root to see whether it still
exists — and nothing else. It starts no process, writes no file, prints nothing,
and never exits. Rendering, exit codes, and refusal wording belong to camp's CLI
layer; a question answered here must be answerable identically from a test, a
listing, and a launch, which it cannot be if the answer is a message on a
terminal. `tools/camp/tests/test_launch_recovery.py` asserts that boundary over
this file's own AST rather than trusting this paragraph.

ONE NAME RULE, ONE CALLER. A session's name component — the middle field of
the tmux session name `camp-<component>-<uuid8>` — is derived from the
directory the session is rooted at, by :func:`_name_component`, and by nothing
else. :func:`session_candidates` is the one caller (via :func:`_build_candidate`):
it shows the operator an addressable name for a session camp did not launch
and keeps no record of. A launch names its own tmux session from the slug it
already knows (`camp.launch.naming.workspace_session_name`), never by
resolving a cwd back to one — this rule exists for the opposite direction,
recovering a name from a directory when camp has no record to read one from.

The rule is a RESOLVED-PATH TEST, and nothing more. A cwd equal to or under
`central_state_dir(<group>)/worktrees/<slug>`, for any group in the configs
handed in, yields that `<slug>`; anything else yields the cwd's own basename. No
manifest is consulted and no directory need exist: resolution is NON-STRICT
precisely so a session whose root has since been torn down still derives the
same name it had while it lived. Both sides of the comparison are fully
resolved, so a symlink cannot make a workspace look like an unrelated directory
(or the reverse).

THE ADDRESSABLE POOL IS A UNION. :func:`session_candidates` maps the harness's
on-disk transcripts and its currently-running live records to one pool, keyed
by session id — a session present in both yields exactly ONE candidate, marked
live. The union is what keeps a live session with no transcript on disk from
reading as "no such session"; a caller that looked only at transcripts would
answer "not found" for a session running right now, which is the worst
available answer. This is the pool every surface that asks "which sessions are
there" reads — the teardown guard, `camp transfer`'s conversation listing — so
no surface derives a different answer from the same two seams.
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

    A group whose config carries no name is skipped rather than raising, as is
    one whose name camp refuses to resolve a state directory for: the name
    rule's contract is to answer, and one malformed entry must not be able to
    stop a listing from naming every other session.

    A `worktrees` that resolves anywhere but where camp would have created it is
    skipped too, and that skip is load-bearing rather than tidy. This container
    is what the whole answer is measured against, so a symlink standing in its
    place redefines "camp-managed" for every question asked afterwards — pointed
    at a root, it would make every directory on the machine answer as a camp
    workspace, and the eligibility gate is skipped for exactly those. camp
    created this directory or it did not; a link claiming to be it is neither.
    """
    containers = []
    for group in groups:
        name = (group.get("group") or {}).get("name")
        if not name:
            continue
        try:
            base = central_state_dir(name, env=dict(env)).resolve()
        except Exception:
            continue
        container = (base / "worktrees").resolve()
        if container != base / "worktrees":
            continue
        containers.append(container)
    return tuple(containers)


def _relative_parts(resolved: Path, containers: Iterable[Path]) -> tuple[str, ...] | None:
    """*resolved* as path components below whichever container holds it, or ``None``.

    *resolved* and *containers* are both already fully resolved. The `worktrees`
    container itself is not a workspace — there is nothing below it there — so it
    answers ``None``.

    The single traversal both halves of the name rule read: the boolean half
    wants only the first component, the naming half wants the member under it,
    and deriving them from one answer is what keeps them from disagreeing about
    which container matched.
    """
    for container in containers:
        try:
            relative = resolved.relative_to(container)
        except ValueError:
            continue
        if relative.parts:
            return relative.parts
    return None


#: How far below the workspace the name rule reads. The workspace slug names the
#: workspace; one more component names the member repo inside it, which is what
#: tells two workers in one multi-repo workspace apart. Nothing below that adds
#: identity — a session in `member/src/pkg` belongs to `member` — and every extra
#: segment costs an operator typing the tmux name.
_NAME_DEPTH = 2


def _name_component(resolved: Path, containers: Iterable[Path]) -> str:
    """The name rule itself, over an already-resolved path.

    Inside a camp workspace: the slug, plus the member directory under it when
    the path is in one. Anywhere else: the basename.

    The result is a single component by the time it reaches a tmux name — the
    join happens here so that :func:`sanitize_name_component` folds the whole
    thing at once, and a member whose directory carries a tmux target separator
    cannot smuggle one through by being joined after the fold.
    """
    parts = _relative_parts(resolved, containers)
    if parts is None:
        return resolved.name
    return "-".join(parts[:_NAME_DEPTH])


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


def printable_path(path) -> str:
    """A path from outside camp, rendered so it cannot forge camp's own output.

    Paths reach camp from a transcript the harness wrote and from the harness's
    own listing output, and camp does not get to assume either holds a plain
    path. A raw control character here is not cosmetic: an embedded newline
    turns one row into two, or one refusal line into two, and a carriage return
    plus an erase sequence rewrites what was already printed. Either way the
    operator reads something camp never said — a session that does not exist,
    or a reason camp did not give.

    Escaping rather than refusing, because the path is the one fact that tells
    two candidates apart; a row that will not name a directory is not safer, it
    is just useless. C1 and the bidi overrides go too: the first is invisible
    under UTF-8 and the second visually reorders a path without changing it.

    The JSON output needs no equivalent — `json.dumps` escapes these already.
    """
    escaped = {c: f"\\x{c:02x}" for c in range(0x20)}
    escaped[0x7F] = "\\x7f"
    escaped.update({c: f"\\x{c:02x}" for c in range(0x80, 0xA0)})
    escaped.update({c: f"\\u{c:04x}" for c in (0x202A, 0x202B, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069)})
    return str(path).translate(escaped)


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

    # Both halves are folded, and by the same rule the launch engine composes the
    # tmux name with. The id half is not a formality: an id reaches camp as a
    # transcript FILENAME, so it carries whatever the filesystem allowed, and a
    # dot inside the first eight characters reads to tmux as a window/pane
    # separator — a name it will create and then refuse to address.
    short_id = sanitize_name_component(session_id[:8])
    if root is None:
        derived_name = f"camp-{short_id}"
    else:
        component = sanitize_name_component(_name_component(root.resolve(), containers))
        derived_name = f"camp-{component}-{short_id}"

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


def session_candidates(
    *,
    transcripts: Iterable[Any],
    live_records: Iterable[Any],
    groups: Iterable[dict[str, Any]],
    env: Mapping[str, str],
    now: datetime | None = None,
) -> tuple[SessionCandidate, ...]:
    """The addressable pool: enumerated *transcripts* UNION the live *live_records*.

    Keyed by session id and by nothing else, so a session present in both halves
    yields exactly ONE candidate, marked live. This is the pool every surface
    that asks "which sessions are there" reads — the teardown guard and `camp
    transfer`'s conversation listing — so no surface gets to derive a
    different answer from the same two seams.

    Ordering is FRESHEST FIRST, with the age-less (live-only) candidates after
    them, so a caller listing candidates gets a stable order it did not impose.
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
    candidates.sort(key=lambda c: (c.age_seconds is None, c.age_seconds or 0.0, c.session_id))
    return tuple(candidates)


