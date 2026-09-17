"""The tmux seam: every tmux invocation camp makes, in one place.

Camp talks to tmux through exactly this module. `Tmux` answers the three
tri-state questions the stop engine was built around (`has_session`,
`pane_command`, `list_sessions`) and owns every other tmux invocation camp
performs — spawning a session (`spawn_session`), stating its environment
(`set_environment`), reading its pane (`capture_pane`), and signalling it
(`kill_session`). Nothing outside this module builds a `["tmux", ...]` argv
of its own; a caller that needs tmux constructs (or is handed) a `Tmux` and
asks it.

The `=`-target property
------------------------
`=` is a *target* prefix, not a name prefix. Measured on tmux 3.7c: without
it, `-t` resolves by prefix match, and a query for one session's name can
answer for a different session whose name merely starts with it — the one
thing a targeted question or signal must never do. With it, tmux requires an
exact name. `-s` (naming a NEW session, at creation) takes a session NAME,
never a target — `tmux new-session -d -s '=weird'` succeeds and creates a
session literally named `=weird`, so qualifying `-s` the way `-t` is
qualified would prefix every session camp creates and make every later
`=<name>` target miss it.

This is implemented as one property over targets, :func:`target`, called by
every method below that takes a `-t` operand — `has_session`,
`pane_command`, `kill_session`, `set_environment`, `capture_pane` — and by
`camp.host.handoff.local_argv`, the one tmux invocation that bypasses this
class entirely (an interactive `exec`, which cannot go through
`subprocess.run`). `spawn_session` is the one method that does NOT call
`target` — it names a session with `-s`, never targets one.

The tri-state contract
-----------------------
`has_session` answers `True` / `False` / `None` (tmux did not answer at
all — a timeout or an unlaunchable binary). `list_sessions` answers a
:class:`SessionListing` only when tmux's non-zero exit is the specific
"no server running" stderr shape (:data:`_NO_SERVER_STDERR_RE`); every other
non-zero exit, and an unanswerable `_run`, is :data:`UNANSWERED`. Folding
"tmux did not answer" into "tmux answered no" would report a hung or
unreachable tmux as a completed, empty state — the one thing every caller of
this seam must never be told.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Mapping, Sequence

#: Bound on any single tmux call that does not name its own timeout.
TMUX_TIMEOUT_SECONDS = 5.0


class _Unanswered:
    """The sentinel a tmux question comes back with when tmux did not answer.

    Distinct from ``None``, which is an ANSWER — "there is no such pane" or
    "there is no such session". A question that came back with nothing known
    must never share a branch with one that came back with a fact.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNANSWERED"


UNANSWERED = _Unanswered()


@dataclass(frozen=True)
class TmuxSession:
    """One session tmux reported, as its name and live window count."""

    name: str
    windows: int


@dataclass(frozen=True)
class SessionListing:
    """Every session tmux holds right now, as answered by
    :meth:`Tmux.list_sessions`.

    ``sessions`` is empty when tmux genuinely answered "no server running" —
    the answered-empty case, never folded into :data:`UNANSWERED`. ``dropped``
    counts rows tmux printed that could not be parsed (no delimiter, or a
    non-numeric window count) and were excluded from ``sessions`` without
    failing the rest of the answer, so a caller can tell "one row was
    unparseable" apart from "the answer was empty".
    """

    sessions: tuple[TmuxSession, ...]
    dropped: int = 0


#: The whole stderr line tmux prints for the non-zero exit that means "no
#: server is running" — `error connecting to <socket> (No such file or
#: directory)`, confirmed against tmux 3.7c. Matched as that shape rather
#: than on the trailing phrase alone, which any number of unrelated
#: failures also carry (a config file tmux could not source, a wrapper
#: script's own complaint). Every OTHER non-zero exit (an unsafe socket
#: directory, an unreachable socket, or any stderr not yet observed) is an
#: outage and must never be read as an empty listing.
_NO_SERVER_STDERR_RE = re.compile(
    r"error connecting to .*\(No such file or directory\)"
)


def target(name: str) -> str:
    """The `-t` target form of a session name: always `=`-exact.

    See the module docstring — this is the one place the `=` rule is
    stated. Every `-t` operand this module (and `camp.host.handoff`) builds
    goes through this function; none inlines the prefix by hand.
    """
    return f"={name}"


class Tmux:
    """Every tmux invocation camp performs, behind one seam."""

    def __init__(self, *, timeout: float = TMUX_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout

    def _run(
        self,
        args: Sequence[str],
        *,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess | None:
        kwargs: dict[str, object] = {}
        if env is not None:
            kwargs["env"] = dict(env)
        try:
            return subprocess.run(
                ["tmux", *args],
                capture_output=True,
                text=True,
                timeout=timeout if timeout is not None else self._timeout,
                **kwargs,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

    def has_session(self, name: str) -> bool | None:
        """Exact-name existence, or ``None`` when tmux did not answer.

        The tri-state is load-bearing. A call that timed out or could not be
        launched knows nothing about the session, and folding that into
        ``False`` would report a hung tmux as a completed stop.
        """
        done = self._run(["has-session", "-t", target(name)])
        if done is None:
            return None
        return done.returncode == 0

    def pane_command(self, name: str) -> str | None | _Unanswered:
        """The session's first pane's originating command.

        Tri-state, for the same reason :meth:`has_session` is: ``None`` means
        tmux answered and there is no pane command to read, while
        :data:`UNANSWERED` means tmux never answered at all. Folding the second
        into the first would report a tmux that went quiet between the two
        questions as a foreign pane holding the name, sending the operator
        hunting a squatter that does not exist.
        """
        done = self._run(
            ["list-panes", "-t", target(name), "-F", "#{pane_start_command}"]
        )
        if done is None:
            return UNANSWERED
        if done.returncode != 0:
            return None
        first = done.stdout.splitlines()
        return first[0] if first else None

    def kill_session(
        self, name: str, *, timeout: float | None = None
    ) -> subprocess.CompletedProcess | None:
        """Issue the kill and return tmux's answer, or ``None`` when tmux
        could not be asked at all.

        Most callers discard the result — the stop engine's own evidence of
        success is a re-poll of :meth:`has_session`, never this call's exit
        status — but a caller that wants to explain a failed reclaim (see
        ``launch/session.py``'s post-confirmation-timeout cleanup) can read
        it.
        """
        return self._run(["kill-session", "-t", target(name)], timeout=timeout)

    def list_sessions(self) -> SessionListing | _Unanswered:
        """Every session tmux currently holds, or ``UNANSWERED``.

        Reads ``#{session_windows}|#{session_name}`` — the count first,
        because it is always digits and a session name may legitimately
        contain the delimiter, so the name is parsed as the remainder after
        the FIRST ``|`` and can never be misread as a name-first field.

        Extends this seam's tri-state rather than reusing :meth:`has_session`'s
        contract: a general listing command's non-zero exit has no single
        documented meaning, unlike a scoped existence query's. Only the
        no-server condition on stderr — tmux's own whole
        connect-failure line, :data:`_NO_SERVER_STDERR_RE`, not the
        trailing phrase an unrelated error may also carry — is answered as
        empty; every other non-zero exit, and an unanswerable ``_run``, is
        ``UNANSWERED``.
        """
        done = self._run(["list-sessions", "-F", "#{session_windows}|#{session_name}"])
        if done is None:
            return UNANSWERED
        if done.returncode != 0:
            if _NO_SERVER_STDERR_RE.search(done.stderr or ""):
                return SessionListing(sessions=())
            return UNANSWERED

        sessions: list[TmuxSession] = []
        dropped = 0
        for line in done.stdout.splitlines():
            if not line:
                continue
            count, separator, name = line.partition("|")
            if not separator or not count.isdigit():
                dropped += 1
                continue
            sessions.append(TmuxSession(name=name, windows=int(count)))
        return SessionListing(sessions=tuple(sessions), dropped=dropped)

    def spawn_session(
        self,
        name: str,
        *,
        cwd: object,
        command: Sequence[str],
        env: Mapping[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess:
        """Start a detached session named *name*, running *command* in its
        first pane.

        ``-s`` names the new session — never `=`-qualified, see the module
        docstring's target-vs-name property. Exceptions are NOT swallowed
        here (unlike :meth:`_run`'s tri-state methods): a caller that needs
        to reclaim the name after a timeout, or distinguish an unlaunchable
        tmux from a refused spawn, reads them itself — see
        ``launch/session.py``'s ``launch_session``.
        """
        argv = ["tmux", "new-session", "-d", "-s", name, "-c", str(cwd), *command]
        return subprocess.run(
            argv,
            cwd=str(cwd),
            env=dict(env),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def set_environment(
        self,
        name: str,
        operand: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess | None:
        """State one ``tmux set-environment`` operand against session *name*.

        *operand* is either a removal (``["-r", VAR]``) or an assignment
        (``[KEY, VALUE]``) — this method issues exactly one tmux call per
        operand, the shape ``launch/session.py``'s session-environment
        statement already builds each of its operands as. Returns ``None``
        when tmux could not be asked; the caller decides what to do with a
        non-zero exit.
        """
        return self._run(
            ["set-environment", "-t", target(name), *operand],
            timeout=timeout,
            env=env,
        )

    def capture_pane(
        self, name: str, *, timeout: float | None = None
    ) -> str | None:
        """The raw text currently on session *name*'s pane, or ``None`` when
        tmux could not answer or the session is already gone.

        Returns tmux's stdout verbatim — sanitizing or bounding it for
        display is the caller's business, not this seam's.
        """
        done = self._run(["capture-pane", "-p", "-t", target(name)], timeout=timeout)
        if done is None or done.returncode != 0:
            return None
        return done.stdout
