"""The `camp rm` session teardown guard — derived, never stored.

`camp rm` has no session-liveness precondition of its own. This module supplies
one: removing a workspace that still holds a resumable session destroys the
conversation the workspace exists to hold, and does it irreversibly.

DERIVED, NOT STORED. Nothing is written when a session is parked and nothing is
cleared when it is resumed. The answer is recomputed each time from the same two
seams the resume path reads — the harness's on-disk transcripts and its live
sessions — merged by :func:`recovery.session_candidates` into the one pool every
session surface shares, then filtered to the workspace subtree. A guard with
state of its own would be a second answer that can disagree with the first.

The subtree rule is :func:`recovery.is_workspace_root`, named deliberately: camp
already carries several incompatible readings of "does this workspace exist",
and this guard adds none. A session rooted BELOW the workspace blocks it, because
a group's `[harness] cwd` routinely roots a launch at a member repo and that
session is every bit as lost when the workspace goes.

FAIL CLOSED. If camp cannot enumerate one of the two halves, it does not know
whether anything is rooted there, and :class:`EnumerationUnavailable` is raised
rather than an empty answer returned. Folding "could not tell" into "nothing
blocks" would turn a failure into a reported success on a destructive,
irreversible surface. `--force` remains the operator's override — the guard is
here so the destruction is chosen, not so it is impossible.

The live probe is tri-state at its own seam, for the same reason the stop
engine's is: a probe that could not run at all knows nothing, while a probe that
RAN and answered — even by exiting nonzero, the way a dead `tmux` server reports
itself — has answered. The one exception is a binary that is not installed:
nothing can be running under a harness that is not on the machine, so that is an
answer of zero live sessions rather than an unanswerable seam.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .recovery import SessionCandidate, is_workspace_root, printable_path, session_candidates

#: The live probe's ceiling. A harness that has not answered by now has not
#: answered at all, and a `camp rm` that hangs on the question is its own
#: failure mode.
PROBE_TIMEOUT_SECONDS = 10.0


class EnumerationUnavailable(Exception):
    """Camp could not answer whether sessions are rooted in the workspace."""


def gather_pool(
    harnesses: Sequence[Any],
    *,
    env: Mapping[str, str],
) -> tuple[list, list]:
    """Read (transcripts, live records) from every *harness*, or fail closed.

    Both halves are required from every harness. Unlike the resume path — where
    a narrowed pool costs the operator a candidate and nothing more — a half
    camp could not read here is a session it cannot see, and an unseen session
    is exactly what this guard exists to refuse on.
    """
    if not harnesses:
        raise EnumerationUnavailable(
            "camp cannot name a harness for any configured group, so it cannot "
            "tell whether any session is still rooted in this workspace"
        )

    transcripts: list = []
    live: list = []
    for harness in harnesses:
        transcripts.extend(_probe_transcripts(harness, env))
        live.extend(_probe_live(harness, env))
    return transcripts, live


def _probe_transcripts(harness, env: Mapping[str, str]) -> list:
    try:
        rows = harness.session_transcripts(env=dict(env))
    except Exception as exc:  # noqa: BLE001 — an unreadable store is unknown, not empty
        raise EnumerationUnavailable(
            f"camp could not read the session transcripts of harness "
            f"{_display_name(harness)}: {exc}"
        ) from exc
    if rows is None:
        raise EnumerationUnavailable(
            f"harness {_display_name(harness)} keeps no session transcripts camp "
            "can read, so camp cannot tell which of its sessions are recoverable"
        )
    return list(rows)


def _probe_live(harness, env: Mapping[str, str]) -> list:
    argv = harness.session_enumerate(None)
    if not argv:
        raise EnumerationUnavailable(
            f"harness {_display_name(harness)} has no way to report its live "
            "sessions, so camp cannot tell whether one is running here"
        )
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            env=dict(env),
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        # Nothing can be running under a binary that is not installed.
        return []
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EnumerationUnavailable(
            f"camp could not ask harness {_display_name(harness)} which sessions "
            f"are live: {exc}"
        ) from exc
    if completed.returncode != 0:
        # The probe ran and reported no sessions the only way it can.
        return []
    try:
        return list(harness.parse_session_list(completed.stdout))
    except Exception as exc:  # noqa: BLE001 — an unparsable answer is no answer
        raise EnumerationUnavailable(
            f"camp could not read harness {_display_name(harness)}'s live session "
            f"listing: {exc}"
        ) from exc


def _display_name(harness) -> str:
    return getattr(harness, "name", None) or type(harness).__name__


def blocking_sessions(
    workspace: Path | str,
    *,
    transcripts: Iterable[Any] | None,
    live_records: Iterable[Any] | None,
    groups: Iterable[dict[str, Any]],
    env: Mapping[str, str],
    now: datetime | None = None,
) -> tuple[SessionCandidate, ...]:
    """Every session — live or recoverable — still rooted in *workspace*.

    ``None`` for either pool is the unanswerable case and raises
    :class:`EnumerationUnavailable`; it is never read as an empty pool.

    A candidate whose root camp could not read blocks nothing: camp does not
    know where it ran, and refusing on it would wedge every removal for as long
    as one unreadable transcript survives. A candidate whose root is already
    GONE blocks nothing either — there is nothing left to resume there, and the
    entry an interrupted teardown left behind must not wedge the re-attempt.
    """
    if transcripts is None or live_records is None:
        raise EnumerationUnavailable(
            "camp could not enumerate this harness's sessions, so it cannot tell "
            "whether any is still rooted in this workspace"
        )

    root = Path(workspace).resolve()
    return tuple(
        candidate
        for candidate in session_candidates(
            transcripts=transcripts,
            live_records=live_records,
            groups=groups,
            env=env,
            now=now,
        )
        if _rooted_in(candidate, root, groups, env)
    )


def _rooted_in(
    candidate: SessionCandidate,
    workspace: Path,
    groups: Iterable[dict[str, Any]],
    env: Mapping[str, str],
) -> bool:
    if candidate.root is None or candidate.root_missing:
        return False
    resolved = candidate.root.resolve()
    if not is_workspace_root(resolved, groups, env=env):
        return False
    return resolved == workspace or resolved.is_relative_to(workspace)


def render_block(slug: str, blocking: Sequence[SessionCandidate]) -> str:
    """Render the refusal, naming every session the removal would destroy.

    Each row carries the derived name — the ref `camp kill` and `camp launch
    --resume` both take — and the root, so the operator can tell two sessions
    apart without going looking for them.
    """
    count = len(blocking)
    noun = "session" if count == 1 else "sessions"
    lines = [
        f"camp remove: workspace {slug!r} still holds {count} resumable {noun}; "
        f"removing it would destroy {'it' if count == 1 else 'them'}:"
    ]
    for candidate in blocking:
        state = "running" if candidate.live else "parked"
        lines.append(
            f"  {candidate.derived_name}  {printable_path(candidate.root)}  ({state})"
        )
    lines.append(
        "  stop one with `camp kill <ref>`, or re-run with --force to remove the "
        "workspace and its sessions"
    )
    return "\n".join(lines)
