"""The stop engine — decide and perform the stop of one addressed session.

Given a ref, this module resolves it, verifies camp is the one who launched
what holds the name, refuses the sessions that must never be stopped, kills the
tmux session, and re-polls until the name is gone. It is data-to-data plus one
injected `tmux` seam: nothing here prints, exits, or reads `os.environ`, and
every test drives it with an in-memory tmux.

Resolution is `recovery.resolve_session_ref` unforked, so `camp kill` and
`camp launch --resume` share one resolver and one ambiguity contract. An
`Ambiguous` or `NoMatch` from that resolver is returned as-is rather than
re-wrapped: a ref is never guessed, and the caller renders the same rows the
resume surface already renders.

Ownership, and what it is not
-----------------------------
A name match is not proof of ownership. Before signalling, camp reads the
target pane's start command and requires it to be a command camp itself would
have composed for THIS session — the seam's own argv, behind the seam's own
`env -u` scrub. Two shapes qualify, and both must: `session_launch(...)` for a
freshly launched pane and `session_resume(...)` for one that has been resumed.
The second is not an edge case — every session that has ever been parked and
brought back carries it, which is the steady-state population this verb
creates, and a check bound to the launch shape alone would refuse all of them.
The shapes are composed by asking the harness seam, never spelled here.

ACCEPTED RISK, CARRIED DELIBERATELY: both shapes are public and reproducible. A
process running as the same OS user that knows a target's derived name and
session id can spawn a pane reproducing either one and pass this check. That
narrows the exposure — an arbitrary process squatting the name is refused — but
it does not close it, and this check is NOT an authorization boundary. Closing
it properly needs verified process ancestry of the harness binary, which is a
spec-level change and not this module's business. A later reader must not read
the check as settling the provenance question.

The self gate carries the same caveat, and more sharply: the caller's own
session id is read from the environment the harness published it into
(`identity.current_session_id`). A caller-supplied environment variable is a
CONVENIENCE CHECK, not an authorization boundary — anything able to set that
variable can clear or forge it. It exists so an operator does not saw off the
branch they are sitting on, not to stop anyone determined.

The already-down oracle
-----------------------
Two readings disagree destructively, so exactly one is pinned here: a candidate
that is not live AND owns no tmux session is already-down (success, idempotent);
one that is live with no tmux session is refused as one camp did not launch;
one whose tmux session exists but is not live is killed anyway, to release the
name the resume path leans on as its collision backstop.

Success is absence, not issuance. Both existing kill sites in camp discard
their result without re-polling; this one polls the name until it is gone and
reports a distinct failure if it never is.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .identity import current_session_id
from .recovery import Resolution, Resolved, SessionCandidate, resolve_session_ref

#: Bound on every individual tmux call. tmux must never be able to hang the
#: verb: a stop that cannot answer is reported, not waited on forever.
TMUX_TIMEOUT_SECONDS = 5.0

#: How long to keep re-polling for the name's absence after the kill, and how
#: often. tmux tears the session down server-side as the call returns, so this
#: is a small budget for a busy server rather than a wait for a process to die.
POLL_TIMEOUT_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.1

#: Where the concierge supervisor publishes the id of the anchor session,
#: under its own state dir. camp reads it; camp never writes it. The directory
#: is resolved through `trailhead.paths` (Axiom 4), which spells the same rule
#: the supervisor itself applies: `CONCIERGE_STATE_DIR`, else
#: `XDG_STATE_HOME/concierge`, else `~/.local/state/concierge`.
ANCHOR_APP = "concierge"
ANCHOR_SESSION_ID_FILENAME = "session_id"

REFUSED_ANCHOR = "anchor"
REFUSED_SELF = "self"
REFUSED_NOT_CAMP_LAUNCHED = "not-camp-launched"
#: tmux itself did not answer — a timed-out or unlaunchable call. Absence of
#: the name is the ONLY evidence this engine accepts for a stop, so a question
#: that came back with no answer can never be read as absence: that would turn
#: a hung tmux into a reported success. Distinct from every other reason
#: because nothing about the SESSION is known here, only about tmux.
REFUSED_TMUX_UNANSWERED = "tmux-unanswered"

#: Live, but owning no tmux session under its derived name — the second branch
#: of the pinned oracle. Distinct from a foreign pane holding the name, because
#: the operator's next move differs: there is nothing here for camp to signal.
REFUSED_LIVE_WITHOUT_SESSION = "live-without-session"


@dataclass(frozen=True)
class StopOutcome:
    """Base of the closed set of stop outcomes. Never returned itself."""


@dataclass(frozen=True)
class Stopped(StopOutcome):
    """The session was killed and the name no longer enumerates."""

    candidate: SessionCandidate


@dataclass(frozen=True)
class AlreadyDown(StopOutcome):
    """Nothing to stop: not live, and owning no tmux session. Success."""

    candidate: SessionCandidate


@dataclass(frozen=True)
class StillPresent(StopOutcome):
    """The kill was issued and the name is still there. The memory was not reclaimed."""

    candidate: SessionCandidate


@dataclass(frozen=True)
class Refused(StopOutcome):
    """camp will not signal this candidate. ``reason`` is one of the REFUSED_* constants."""

    candidate: SessionCandidate
    reason: str


class Tmux:
    """The tmux seam: the three questions the engine asks of a session name."""

    def __init__(self, *, timeout: float = TMUX_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout

    def _run(self, args: Sequence[str]) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(
                ["tmux", *args],
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

    def has_session(self, name: str) -> bool | None:
        """Exact-name existence, or ``None`` when tmux did not answer.

        `=` is not decoration: without it tmux prefix-matches, and a prefix
        match would answer for a different session — the one thing this
        question must never do.

        The tri-state is load-bearing. A call that timed out or could not be
        launched knows nothing about the session, and folding that into
        ``False`` would report a hung tmux as a completed stop.
        """
        done = self._run(["has-session", "-t", f"={name}"])
        if done is None:
            return None
        return done.returncode == 0

    def pane_command(self, name: str) -> str | None:
        """The session's first pane's originating command, or None."""
        done = self._run(
            ["list-panes", "-t", f"={name}", "-F", "#{pane_start_command}"]
        )
        if done is None or done.returncode != 0:
            return None
        first = done.stdout.splitlines()
        return first[0] if first else None

    def kill_session(self, name: str) -> None:
        """Issue the kill. The result is deliberately unread — absence of the
        name afterwards is the only evidence this engine accepts."""
        self._run(["kill-session", "-t", f"={name}"])


def anchor_session_id(env: Mapping[str, str]) -> str | None:
    """The concierge anchor's session id, as the supervisor recorded it.

    The anchor is the operator's sole phone-side entry point and stopping it is
    an unrecoverable lockout. It IS inside the resolution pool — every derived
    name carries an unconditional `camp-` prefix — so the exclusion has to be a
    gate camp performs, not a property it inherits from the anchor happening to
    be unreachable.

    Unreadable for any reason is ``None``: camp does not fabricate an id it
    could not read, and the caller's other gates still apply.
    """
    from trailhead.paths import PathResolutionError, state_dir

    try:
        directory = state_dir(ANCHOR_APP, env=dict(env))
        value = (directory / ANCHOR_SESSION_ID_FILENAME).read_text(encoding="utf-8").strip()
    except (PathResolutionError, OSError):
        return None
    return value or None


def _owning_commands(harness, candidate: SessionCandidate) -> tuple[tuple[str, ...], ...]:
    """Every pane command camp itself would have composed for this session.

    Both shapes, asked of the seam rather than spelled here: the launch argv and
    the resume argv, each behind the scrub camp applies at spawn time. The
    workspace argument is the candidate's own root; a harness that roots a
    launch on it gets the truth, and one that ignores it (Claude Code does) is
    unaffected either way.
    """
    scrub = harness.session_launch_env_unset() or ()
    prefix = ["env"]
    for name in scrub:
        prefix += ["-u", name]

    workspace = candidate.root if candidate.root is not None else Path("/")
    shapes: list[tuple[str, ...]] = []
    for build in (
        lambda: harness.session_launch(
            workspace, candidate.session_id, session_name=candidate.derived_name
        ),
        lambda: harness.session_resume(candidate.session_id),
    ):
        try:
            argv = build()
        except Exception:
            argv = None
        if argv:
            shapes.append(tuple(prefix) + tuple(argv))
    return tuple(shapes)


def _is_camp_launched(harness, candidate: SessionCandidate, pane_command: str | None) -> bool:
    if not pane_command:
        return False
    try:
        observed = tuple(shlex.split(pane_command))
    except ValueError:
        return False
    return observed in _owning_commands(harness, candidate)


def stop_session(
    ref: str,
    *,
    harness,
    transcripts: Iterable[Any],
    live_records: Iterable[Any],
    groups: Iterable[dict[str, Any]],
    env: Mapping[str, str],
    tmux: Any | None = None,
    now=None,
    sleep: Callable[[float], None] = time.sleep,
    poll_timeout: float = POLL_TIMEOUT_SECONDS,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> Resolution | StopOutcome:
    """Stop the one session *ref* addresses. See the module docstring.

    Returns the resolver's own ``Ambiguous`` / ``NoMatch`` when the ref does not
    address exactly one session, and otherwise one of the ``StopOutcome`` kinds.
    """
    tmux = tmux if tmux is not None else Tmux()

    resolution = resolve_session_ref(
        ref,
        transcripts=transcripts,
        live_records=live_records,
        groups=groups,
        env=env,
        now=now,
    )
    if not isinstance(resolution, Resolved):
        return resolution

    candidate = resolution.candidate

    if candidate.session_id == anchor_session_id(env):
        return Refused(candidate, REFUSED_ANCHOR)

    if candidate.session_id == current_session_id(dict(env)):
        return Refused(candidate, REFUSED_SELF)

    name = candidate.derived_name
    present = tmux.has_session(name)
    if present is None:
        return Refused(candidate, REFUSED_TMUX_UNANSWERED)
    if not present:
        # The pinned oracle: absent tmux session AND not live is already-down;
        # absent tmux session while still live is a session camp did not launch
        # and must not be reported as reclaimed.
        if candidate.live:
            return Refused(candidate, REFUSED_LIVE_WITHOUT_SESSION)
        return AlreadyDown(candidate)

    if not _is_camp_launched(harness, candidate, tmux.pane_command(name)):
        return Refused(candidate, REFUSED_NOT_CAMP_LAUNCHED)

    tmux.kill_session(name)

    waited = 0.0
    while True:
        present = tmux.has_session(name)
        if present is None:
            return Refused(candidate, REFUSED_TMUX_UNANSWERED)
        if not present:
            return Stopped(candidate)
        if waited >= poll_timeout:
            return StillPresent(candidate)
        sleep(poll_interval)
        waited += poll_interval
