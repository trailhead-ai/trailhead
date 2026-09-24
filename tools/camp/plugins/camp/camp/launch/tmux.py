"""The tmux seam: every tmux invocation camp makes, in one place.

Camp talks to tmux through exactly this module. `Tmux` answers the tri-state
questions the stop engine and reconciliation are built around (`has_session`,
`pane_command`, `list_sessions`, `list_windows`) and owns every other tmux
invocation camp performs — spawning a session with an explicit command and a
scrubbed environment (`spawn_session`), starting a bare login-shell window over it
(`new_session`), stating its environment (`set_environment`), and signalling it
(`kill_session`). Nothing outside
this module builds a `["tmux", ...]` argv of its own; a caller that needs
tmux constructs (or is handed) a `Tmux` and asks it.

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
`pane_command`, `kill_session`, `set_environment` — and by
`camp.host.handoff.door_argv`, the one tmux invocation that bypasses this
class entirely (an interactive `exec`, which cannot go through
`subprocess.run`). `spawn_session` is the one method that does NOT call
`target` — it names a session with `-s`, never targets one.

`set_option` and `show_option` are a second exception: they take an
already-qualified target STRING from the caller rather than applying
:func:`target` themselves, because one of their callers (the session-start
conversation capture) addresses a session by the tmux-minted numeric id
(e.g. `$3`) :meth:`Tmux.pane_window` answers, which `=`-name-prefix
qualification does not apply to. See :meth:`Tmux.set_option`'s docstring.
:meth:`Tmux.pane_window` addresses a PANE id (`%12`) verbatim for the same
reason.

The tri-state contract
-----------------------
`has_session` answers `True` / `False` / `None` (tmux did not answer at
all — a timeout or an unlaunchable binary). `list_sessions` answers a
:class:`SessionListing` only when tmux's non-zero exit is one of the two
"no server running" stderr shapes (:data:`_NO_SERVER_STDERR_RE`); every other
non-zero exit, and an unanswerable `_run`, is :data:`UNANSWERED`. Folding
"tmux did not answer" into "tmux answered no" would report a hung or
unreachable tmux as a completed, empty state — the one thing every caller of
this seam must never be told.

`list_windows` extends the same shape one level down: a :class:`WindowListing`
of a session's windows when tmux answered, `None` when tmux answered that the
named session does not exist (the specific `can't find session` stderr shape),
and :data:`UNANSWERED` for every other non-zero exit or an unanswerable
`_run`. A caller that cannot get an answer must change nothing — "could not
tell" is never read as "no windows".
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Mapping, Sequence

#: Bound on any single tmux call that does not name its own timeout.
TMUX_TIMEOUT_SECONDS = 5.0

#: Environment override for the budget above, in seconds.
#:
#: The budget is sized for a busy tmux server, so a caller driving a question
#: tmux will never answer waits all of it before seeing the refusal. That is
#: right for a real call and pure dead time for a test asserting the refusal,
#: so the default is readable from the environment — the same shape as the
#: other `CAMP_TEST_*` seams. Only the default: a caller passing `timeout=`
#: still gets exactly what it asked for.
_TMUX_TIMEOUT_ENV = "CAMP_TEST_TMUX_TIMEOUT_SECONDS"


def resolve_budget(name: str, shipped: float, env: Mapping[str, str] | None = None) -> float:
    """Return the budget *name* overrides in *env*, else *shipped*.

    An absent, unparseable or non-positive value leaves the shipped budget in
    place: a stray or malformed setting must not be able to shrink a real
    call's window to nothing, which would report a busy tmux as a failure.
    """
    raw = (os.environ if env is None else env).get(name)
    if raw is None:
        return shipped
    try:
        override = float(raw)
    except ValueError:
        return shipped
    return override if override > 0 else shipped


def _strip_one_trailing_newline(text: str) -> str:
    """Drop a single trailing newline from a tmux answer, if present.

    Deliberately not `rstrip`/`splitlines`: a tmux window name (and an option
    value) is user-influenced text that may legitimately end in whitespace or
    contain newlines of its own, and an empty answer must stay the empty
    string rather than becoming an empty list to index into.
    """
    return text[:-1] if text.endswith("\n") else text


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
    """One session tmux reported: its name, live window count, and the epoch
    second tmux itself last recorded activity on it (`#{session_activity}`).
    Every session :meth:`Tmux.list_sessions` parses carries ``activity``;
    it is `None` only for a `TmuxSession` a caller builds directly without
    one."""

    name: str
    windows: int
    activity: int | None = None


@dataclass(frozen=True)
class NewWindowResult:
    """What :meth:`Tmux.new_window` reports on a successful create.

    Both fields are read back from tmux on the SAME creating call — never a
    value this seam predicted or echoed from its own arguments — so a caller
    that records ``window_name`` is recording what tmux actually holds, not
    what it asked tmux for. See :meth:`Tmux.new_window`'s docstring for why
    that distinction is load-bearing.
    """

    window_id: str
    window_name: str


#: The exact stderr shape tmux prints for a `new-session` refused because
#: the name is already live, confirmed against tmux 3.7c. Matched as a
#: substring of the whole stderr line, never as the whole line, because
#: tmux does not guarantee nothing precedes it. The single spelling both
#: `new_session_with_window` and `workspace_session.py`'s caller read —
#: owned here so the two never drift apart.
DUPLICATE_SESSION_MARKER = "duplicate session:"


class _DuplicateSession:
    """The sentinel :meth:`Tmux.new_session_with_window` answers when tmux
    refused to create the session because a live session already holds the
    requested name — not a failure, another camp process won the race.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "DUPLICATE"


DUPLICATE = _DuplicateSession()


@dataclass(frozen=True)
class NewSessionWindowFailure:
    """A failed :meth:`Tmux.new_session_with_window` call, carrying tmux's
    own stderr verbatim and unsummarized — never folded into
    :data:`DUPLICATE`, which is reserved for the one recognised stderr
    shape.
    """

    stderr: str


@dataclass(frozen=True)
class NewWindowFailure:
    """A failed :meth:`Tmux.new_window_with_reason` call, carrying tmux's
    own stderr verbatim and unsummarized — the same shape
    :class:`NewSessionWindowFailure` carries for the session-creating call.
    """

    stderr: str


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


#: The stderr shapes tmux prints for the non-zero exit that means "no
#: server is running", confirmed against tmux 3.7c:
#: `error connecting to <socket> (No such file or directory)` when the
#: socket path never existed (ENOENT), and `no server running on <socket>`
#: when the path exists but nothing is listening on it (ECONNREFUSED, e.g.
#: a stale socket file left behind by a server that already exited).
#: Matched as either whole-line shape rather than on a trailing phrase
#: alone, which any number of unrelated failures also carry (a config file
#: tmux could not source, a wrapper script's own complaint). Every OTHER
#: non-zero exit (an unsafe socket directory, an unreachable socket, or any
#: stderr not yet observed) is an outage and must never be read as an empty
#: listing.
_NO_SERVER_STDERR_RE = re.compile(
    r"error connecting to .*\(No such file or directory\)"
    r"|no server running on \S"
)


@dataclass(frozen=True)
class TmuxWindow:
    """One window tmux reported for a session, as answered by
    :meth:`Tmux.list_windows`.

    ``current_path`` and ``current_command`` are the ACTIVE pane's — the one
    a plain `tmux attach` would land in — not every pane the window may hold;
    a multi-pane window is out of scope for reconciliation and the stop
    preview, both of which only need "what is this window doing right now".
    """

    window_id: str
    current_path: str
    current_command: str
    name: str


#: The whole stderr line tmux prints for `list-windows -t <target>` against a
#: session that does not exist — `can't find session: <name>`, confirmed
#: against tmux 3.7c. Distinct from :data:`_NO_SERVER_STDERR_RE`: a missing
#: SESSION on a live server is an answer ("no such session"), while a missing
#: SERVER is a different question this method never has to ask, since a
#: caller of `list_windows` already has a session name to check.
_CANT_FIND_SESSION_STDERR_RE = re.compile(r"can't find session:")


@dataclass(frozen=True)
class WindowListing:
    """Every window tmux holds for one session right now, as answered by
    :meth:`Tmux.list_windows`.

    Mirrors :class:`SessionListing`'s shape: ``windows`` in tmux's own order,
    ``dropped`` counting rows that did not split into exactly four
    tab-separated fields — malformed rows are excluded rather than failing
    the whole answer, so a caller can tell "one row was unparseable" apart
    from "the session has no windows".
    """

    windows: tuple[TmuxWindow, ...]
    dropped: int = 0


#: The `list-windows -F` format this seam reads: window id, the active
#: pane's current directory, the active pane's current foreground command,
#: then the window's own name — name LAST, because tmux refuses a window
#: name containing a tab or a newline (confirmed against tmux 3.7c,
#: `invalid window name`), so a tab-separated format with the name last
#: splits unambiguously no matter what a name legitimately contains (a
#: space, a `|`). A literal, never built from an interpolated name.
_LIST_WINDOWS_FORMAT = "#{window_id}\t#{pane_current_path}\t#{pane_current_command}\t#{window_name}"


@dataclass(frozen=True)
class PaneWindow:
    """Where one pane sits, as answered by :meth:`Tmux.pane_window`: its
    window's id and name, its session's tmux-minted id (``$3``), and the
    directory its current process is running in."""

    window_id: str
    session_id: str
    current_path: str
    window_name: str


#: The `display-message -p` format :meth:`Tmux.pane_window` reads — the
#: window name LAST, for the same reason :data:`_LIST_WINDOWS_FORMAT` puts it
#: last: tmux refuses a name containing a tab, and the remaining fields
#: never contain one in practice, so a tab split is unambiguous.
_PANE_WINDOW_FORMAT = "#{window_id}\t#{session_id}\t#{pane_current_path}\t#{window_name}"


def target(name: str) -> str:
    """The `-t` target form of a session name: always `=`-exact.

    See the module docstring — this is the one place the `=` rule is
    stated. Every `-t` operand this module (and `camp.host.handoff`) builds
    goes through this function; none inlines the prefix by hand.
    """
    return f"={name}"


class Tmux:
    """Every tmux invocation camp performs, behind one seam."""

    def __init__(self, *, timeout: float | None = None) -> None:
        self._timeout = (
            timeout
            if timeout is not None
            else resolve_budget(_TMUX_TIMEOUT_ENV, TMUX_TIMEOUT_SECONDS)
        )

    def _run(
        self,
        args: Sequence[str],
        *,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
    ) -> subprocess.CompletedProcess | None:
        done, _reason = self._run_with_reason(
            args, timeout=timeout, env=env, stdin_text=stdin_text
        )
        return done

    def _run_with_reason(
        self,
        args: Sequence[str],
        *,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
    ) -> tuple[subprocess.CompletedProcess | None, str | None]:
        """Same call :meth:`_run` makes, plus the exception's own message
        when the call could not complete at all — the one piece of
        information :meth:`_run` throws away. Nothing but
        :meth:`has_session_with_reason` needs that message today, so every
        other caller stays on the reason-less :meth:`_run`.
        """
        kwargs: dict[str, object] = {}
        if env is not None:
            kwargs["env"] = dict(env)
        if stdin_text is not None:
            kwargs["input"] = stdin_text
        try:
            return (
                subprocess.run(
                    ["tmux", *args],
                    capture_output=True,
                    text=True,
                    timeout=timeout if timeout is not None else self._timeout,
                    **kwargs,
                ),
                None,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, str(exc)

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

    def has_session_with_reason(self, name: str) -> tuple[bool | None, str | None]:
        """Same tri-state as :meth:`has_session`, plus tmux's own words for
        *why* when the answer is ``None`` — the message of the `OSError` or
        `subprocess.TimeoutExpired` that kept the call from completing at
        all, the only way this ever answers ``None``. The reason is
        ``None`` whenever the answer itself is not — a completed call, of
        whatever exit status, is not something this seam summarizes.

        The one consumer is the door's `TMUX_UNANSWERED` refusal
        (`camp.launch.workspace_session.create_or_connect_workspace_session`),
        which has an operator to explain the refusal to; every other
        `has_session` caller stays on the plain bool/None seam, since
        adding an unused reason there would be seam-widening with no
        consumer.
        """
        done, reason = self._run_with_reason(["has-session", "-t", target(name)])
        if done is None:
            return None, reason
        return done.returncode == 0, None

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

    def new_window(
        self,
        name: str,
        *,
        cwd: object,
        window_name: str,
        command: Sequence[str],
        timeout: float | None = None,
    ) -> NewWindowResult | None | _Unanswered:
        """Create a window in session *name*, rooted at *cwd*, named
        *window_name*, running *command* (empty for the default shell), and
        return the window id AND name tmux itself assigned.

        Both are read back on this same invocation — `-P -F '#{window_id}
        #{window_name}'` — never a second `list-windows` call: verified
        (tmux 3.7c) that tmux prints the format result on the creating call
        itself, with or without a command and `-c`/`-n`, so there is no
        create-then-read race to resolve. The two fields are joined by a
        single space and parsed by splitting on the FIRST space only: a
        tmux window id (`@7`) never itself contains a space, while a window
        name is user-influenced text that may, so parsing anywhere but the
        first space could truncate a name — verified against real tmux 3.7c
        that a name holding spaces round-trips whole this way. `-t` is
        `=`-qualified through :func:`target`, same as every other `-t`
        operand this seam builds — see the module docstring's `=`-target
        property.

        The name is READ BACK rather than trusted from *window_name*
        because what tmux actually assigned is the only value a caller may
        ever again address or record — asking for a name and recording that
        same string in parallel would let the two silently diverge the
        moment tmux does not honor the request verbatim.

        Tri-state, matching :meth:`pane_command`'s contract:
        :class:`NewWindowResult` on success; `None` when tmux answered with
        a non-zero exit (the session did not exist, say) and so created no
        window; :data:`UNANSWERED` when tmux could not be asked at all.
        Folding the last two together would report a hung or unreachable
        tmux as an ordinary create failure — the one thing this seam's
        tri-state exists to keep apart.

        Implemented over :meth:`new_window_with_reason`, which answers the
        same success and :data:`UNANSWERED` cases and folds a
        :class:`NewWindowFailure` down to the bare `None` this method has
        always returned — this method's tri-state is unchanged by that
        method's existence; a caller that needs tmux's own stderr on a
        refusal reaches for `new_window_with_reason` instead.
        """
        answer = self.new_window_with_reason(
            name, cwd=cwd, window_name=window_name, command=command, timeout=timeout
        )
        if isinstance(answer, NewWindowFailure):
            return None
        return answer

    def new_window_with_reason(
        self,
        name: str,
        *,
        cwd: object,
        window_name: str,
        command: Sequence[str],
        timeout: float | None = None,
    ) -> NewWindowResult | NewWindowFailure | _Unanswered:
        """Same call :meth:`new_window` makes, plus tmux's own stderr,
        verbatim, when the call answers with a non-zero exit — the piece of
        information :meth:`new_window` throws away, needed by a caller (the
        resurrection engine) that reports tmux's own words on a failed
        window rather than a fixed sentence.

        Tri-state: :class:`NewWindowResult` on success; :class:`NewWindowFailure`
        on a completed, non-zero exit; :data:`UNANSWERED` when tmux could
        not be asked at all — the same three cases :meth:`new_window`
        collapses its own `None` from the middle one.
        """
        done = self._run(
            [
                "new-window",
                "-t",
                target(name),
                "-P",
                "-F",
                "#{window_id} #{window_name}",
                "-n",
                window_name,
                "-c",
                str(cwd),
                *command,
            ],
            timeout=timeout,
        )
        if done is None:
            return UNANSWERED
        if done.returncode != 0:
            return NewWindowFailure(stderr=done.stderr or "")
        window_id, _, actual_name = _strip_one_trailing_newline(done.stdout).partition(" ")
        return NewWindowResult(window_id=window_id, window_name=actual_name)

    def pane_window(self, pane: str, *, timeout: float | None = None) -> PaneWindow | None:
        """Where pane *pane* (a tmux pane id, ``%12`` — what tmux exports to
        every pane's processes as ``TMUX_PANE``) sits: a :class:`PaneWindow`,
        or ``None`` when tmux gave no usable answer.

        *pane* is addressed verbatim, never through :func:`target`: a pane
        id is already exact, and ``=``-name qualification does not apply to
        it. ``None`` folds "no such pane", "tmux did not answer", and a
        malformed row together deliberately — the one caller (the
        session-start conversation capture) records nothing in every one of
        those cases, so keeping them apart would be structure no caller
        reads.
        """
        done = self._run(["display-message", "-p", "-t", pane, _PANE_WINDOW_FORMAT], timeout=timeout)
        if done is None or done.returncode != 0:
            return None
        fields = _strip_one_trailing_newline(done.stdout).split("\t")
        if len(fields) != 4:
            return None
        window_id, session_id, current_path, window_name = fields
        return PaneWindow(
            window_id=window_id,
            session_id=session_id,
            current_path=current_path,
            window_name=window_name,
        )

    def kill_session(
        self, name: str, *, timeout: float | None = None
    ) -> subprocess.CompletedProcess | None:
        """Issue the kill and return tmux's answer, or ``None`` when tmux
        could not be asked at all.

        Most callers discard the result — the stop engine's own evidence of
        success is a re-poll of :meth:`has_session`, never this call's exit
        status — but a caller that wants to explain a failure can read it.
        """
        return self._run(["kill-session", "-t", target(name)], timeout=timeout)

    def list_sessions(self) -> SessionListing | _Unanswered:
        """Every session tmux currently holds, or ``UNANSWERED``.

        Reads ``#{session_windows}|#{session_activity}|#{session_name}`` —
        the two digit-only fields first, because a session name may
        legitimately contain the delimiter, so the name is parsed as the
        remainder after the FIRST TWO ``|``s and can never be misread as a
        leading field. ``session_activity`` is tmux's own last-activity
        timestamp (epoch seconds) for the session — read in this SAME call
        rather than a second one, so `camp list`'s "last touched" column
        costs no extra tmux round trip per workspace.

        Extends this seam's tri-state rather than reusing :meth:`has_session`'s
        contract: a general listing command's non-zero exit has no single
        documented meaning, unlike a scoped existence query's. Only the
        no-server condition on stderr — one of tmux's two whole
        connect-failure lines, :data:`_NO_SERVER_STDERR_RE`, not the
        trailing phrase an unrelated error may also carry — is answered as
        empty; every other non-zero exit, and an unanswerable ``_run``, is
        ``UNANSWERED``.
        """
        done = self._run(
            ["list-sessions", "-F", "#{session_windows}|#{session_activity}|#{session_name}"]
        )
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
            count, separator, rest = line.partition("|")
            if not separator or not count.isdigit():
                dropped += 1
                continue
            activity, separator, name = rest.partition("|")
            if not separator or not activity.isdigit():
                dropped += 1
                continue
            sessions.append(
                TmuxSession(name=name, windows=int(count), activity=int(activity))
            )
        return SessionListing(sessions=tuple(sessions), dropped=dropped)

    def list_windows(self, name: str) -> WindowListing | None | _Unanswered:
        """Every window session *name* currently holds, or the answer that
        it does not exist, or ``UNANSWERED``.

        Reads :data:`_LIST_WINDOWS_FORMAT` — id, active-pane directory,
        active-pane command, name last — over a single `list-windows -t
        <target>` call, `=`-qualified through :func:`target` like every
        other `-t` operand this seam builds.

        Tri-state, but shaped differently from :meth:`list_sessions`: a
        session-scoped question DOES have a single documented non-zero-exit
        meaning — `can't find session: <name>` — so that specific stderr
        shape (:data:`_CANT_FIND_SESSION_STDERR_RE`) answers `None`, an
        answer distinct from a :class:`WindowListing`. Every other non-zero
        exit, and an unanswerable `_run`, is :data:`UNANSWERED`: folding a
        hung or unreachable tmux into "no such session" would report a
        session reconciliation cannot ask about as one that was never
        composed.
        """
        done = self._run(
            ["list-windows", "-t", target(name), "-F", _LIST_WINDOWS_FORMAT]
        )
        if done is None:
            return UNANSWERED
        if done.returncode != 0:
            if _CANT_FIND_SESSION_STDERR_RE.search(done.stderr or ""):
                return None
            return UNANSWERED

        windows: list[TmuxWindow] = []
        dropped = 0
        for line in done.stdout.splitlines():
            if not line:
                continue
            fields = line.split("\t")
            if len(fields) != 4:
                dropped += 1
                continue
            window_id, current_path, current_command, window_name = fields
            windows.append(
                TmuxWindow(
                    window_id=window_id,
                    current_path=current_path,
                    current_command=current_command,
                    name=window_name,
                )
            )
        return WindowListing(windows=tuple(windows), dropped=dropped)

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
        tmux from a refused spawn, reads them itself.
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

    def new_session_with_window(
        self,
        name: str,
        *,
        cwd: object,
        window_name: str,
        command: Sequence[str],
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> NewWindowResult | _DuplicateSession | NewSessionWindowFailure:
        """Start a detached session named *name*, whose first window is
        named *window_name*, rooted at *cwd*, and runs *command* — reading
        that window's id and name back on the SAME creating call, exactly
        the way :meth:`new_window` reads a later window's back: `-P -F
        '#{window_id} #{window_name}'`, parsed on the FIRST space only, so
        a window name holding spaces round-trips whole.

        `-s` names the new session and is never `=`-qualified — the same
        target-vs-name property :meth:`spawn_session` observes. `-n` goes
        through the same operand :meth:`new_window` uses.

        A closed, three-way result: :class:`NewWindowResult` on exit 0;
        :data:`DUPLICATE` when stderr carries :data:`DUPLICATE_SESSION_MARKER`
        — another camp process already holds *name*, not a failure; a
        :class:`NewSessionWindowFailure` carrying tmux's verbatim stderr
        otherwise.

        Exceptions from the spawn (`OSError`, `TimeoutExpired`) are NOT
        swallowed — propagate exactly as :meth:`spawn_session`'s do. This is
        the server-starting call, and the caller (the workspace door) already
        folds them the way it folds `spawn_session`'s.
        """
        argv = [
            "tmux",
            "new-session",
            "-d",
            "-s",
            name,
            "-n",
            window_name,
            "-c",
            str(cwd),
            "-P",
            "-F",
            "#{window_id} #{window_name}",
            *command,
        ]
        done = subprocess.run(
            argv,
            cwd=str(cwd),
            env=dict(os.environ) if env is None else dict(env),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout if timeout is not None else self._timeout,
        )
        if done.returncode == 0:
            window_id, _, actual_name = _strip_one_trailing_newline(
                done.stdout
            ).partition(" ")
            return NewWindowResult(window_id=window_id, window_name=actual_name)
        stderr = done.stderr or ""
        if DUPLICATE_SESSION_MARKER in stderr:
            return DUPLICATE
        return NewSessionWindowFailure(stderr=stderr)

    def new_session(
        self,
        name: str,
        *,
        cwd: object,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess:
        """Start a detached session named *name*, holding one window running
        the environment's default shell — no command, no environment scrub.

        A thin wrapper over :meth:`spawn_session` with *command* empty: an
        empty command leaves tmux to start whatever shell it is configured
        with in the pane, which is exactly a login-shell window. This keeps
        the `new-session` argv built in exactly one place — this method
        composes none of its own. *env* defaults to the current process
        environment, unmodified: the workspace door scrubs nothing.
        """
        return self.spawn_session(
            name,
            cwd=cwd,
            command=(),
            env=dict(os.environ) if env is None else env,
            timeout=timeout if timeout is not None else self._timeout,
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
        operand. Returns ``None``
        when tmux could not be asked; the caller decides what to do with a
        non-zero exit.
        """
        return self._run(
            ["set-environment", "-t", target(name), *operand],
            timeout=timeout,
            env=env,
        )

    def respawn_first_pane(
        self, name: str, *, timeout: float | None = None
    ) -> subprocess.CompletedProcess | None:
        """Kill and restart the shell in session *name*'s only pane
        (``tmux respawn-pane -k -t =<name>:``), so it starts again under the
        session's CURRENT environment.

        A pane's environment is fixed when its process starts; a
        ``set-environment`` stated on the session afterwards reaches only
        panes started later. Confirmed against real tmux 3.7c that the
        restarted pane carries the session's assignments and not the names
        it removes. The trailing ``:`` addresses the session's current
        window and pane — the only one a session just created has.

        Returns ``None`` when tmux could not be asked at all.
        """
        return self._run(["respawn-pane", "-k", "-t", f"{target(name)}:"], timeout=timeout)

    def switch_client(
        self, name: str, *, timeout: float | None = None
    ) -> subprocess.CompletedProcess | None:
        """Move the current tmux client to session *name* — ``switch-client``.

        Deliberately never exec'd by any caller: `switch-client` returns
        immediately where `attach-session` blocks, so an exec'd
        `switch-client` leaves the calling pane with no process, and tmux
        tears down the pane, the window, and (when that window was its
        last) the whole source session — see
        `camp/host/handoff.py`'s module docstring and
        `docs/design/the-door-creates-or-connects-a-workspace-session.md`'s
        "Handing over the terminal". The caller runs this as an ordinary
        subprocess and exits on its own result, the same way
        :meth:`kill_session`'s own caller inspects that call's result
        instead of trusting a re-poll alone.

        Returns ``None`` when tmux could not be asked at all.
        """
        return self._run(["switch-client", "-t", target(name)], timeout=timeout)

    def _pane_syntax_target(self, target: str) -> str:
        """Normalize *target* for a `set-option` / `show-options` call.

        Measured on tmux 3.7c: `set-option`/`show-options` parse `-t` as a
        PANE target (`session[:window[.pane]]`), and — unlike `has-session`,
        `display-message`, or `kill-session`, which all accept a bare
        `=name` exact-match session target directly — a bare `=name` with
        no trailing `:` fails these two with `no such session: =name`, even
        though the named session exists. Appending a trailing `:` (an empty
        window/pane component, meaning "the session itself") fixes it for
        both an `=`-qualified name and a raw tmux-minted session id (`$3`);
        confirmed harmless (idempotent) when *target* already ends in `:`.
        """
        return target if target.endswith(":") else f"{target}:"

    def set_option(
        self,
        target: str,
        key: str,
        value: str,
        *,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess | None:
        """State one session-LOCAL option (``tmux set-option -t <target> <key>
        <value>``) — no ``-g``, so the value never leaks past the addressed
        session.

        *target* is the caller's own, already-qualified ``-t`` operand —
        unlike every other method above, this one does NOT apply
        :func:`target` itself. Callers address a session two different ways:
        `create_workspace_session` has a session NAME and passes
        ``target(name)``; the session-start conversation capture (through
        :meth:`show_option`) has only the tmux-minted numeric session id
        (e.g. ``$3``) :meth:`pane_window` answered, which :func:`target`'s
        ``=``-name-prefix qualification does not apply to and must not be
        run through. Either way, *target* is passed through
        :meth:`_pane_syntax_target` first — see its docstring for the
        real-tmux quirk that makes this necessary.

        Returns ``None`` when tmux could not be asked at all; the caller
        decides what to do with a non-zero exit.
        """
        return self._run(
            ["set-option", "-t", self._pane_syntax_target(target), key, value],
            timeout=timeout,
        )

    def show_option(
        self, target: str, key: str, *, timeout: float | None = None
    ) -> str | None:
        """Read back one option's value (``tmux show-options -t <target> -v
        <key>``), or ``None`` when tmux could not answer, the option is
        unset, or the session is gone.

        ``-v`` prints the bare value with no ``key value`` pair to parse.
        *target* is caller-supplied, not qualified here — see
        :meth:`set_option`'s docstring for why, including the
        :meth:`_pane_syntax_target` normalization both methods share.
        """
        done = self._run(
            ["show-options", "-t", self._pane_syntax_target(target), "-v", key],
            timeout=timeout,
        )
        if done is None or done.returncode != 0:
            return None
        return _strip_one_trailing_newline(done.stdout)
