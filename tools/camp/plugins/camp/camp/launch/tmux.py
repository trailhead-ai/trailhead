"""The tmux seam: every tmux invocation camp makes, in one place.

Camp talks to tmux through exactly this module. `Tmux` answers the tri-state
questions the stop engine and reconciliation are built around (`has_session`,
`pane_command`, `list_sessions`, `list_windows`) and owns every other tmux
invocation camp performs — spawning a session with an explicit command and a
scrubbed environment (`spawn_session`), starting a bare login-shell window over it
(`new_session`), stating its environment (`set_environment`), reading its
pane (`capture_pane`), and signalling it (`kill_session`). Nothing outside
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
`pane_command`, `kill_session`, `set_environment`, `capture_pane` — and by
`camp.host.handoff.local_argv`, the one tmux invocation that bypasses this
class entirely (an interactive `exec`, which cannot go through
`subprocess.run`). `spawn_session` is the one method that does NOT call
`target` — it names a session with `-s`, never targets one.

`set_option`, `show_option`, and `display_message` are a second exception:
they take an already-qualified target STRING from the caller rather than
applying :func:`target` themselves, because one of their call sites (the
window-dispatch verb) addresses a session by the tmux-minted numeric id
`#{session_id}` hands it (e.g. `$3`), which `=`-name-prefix qualification
does not apply to. See :meth:`Tmux.set_option`'s docstring.

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


#: The `bind-key` operands that address the one key camp ever rebinds. Shared
#: by :meth:`Tmux.install_window_binding` and :meth:`Tmux.reset_window_binding`
#: so the two can only ever address the SAME table entry — a reset that drifted
#: onto a different key or table would leave camp's binding installed while
#: reporting the key restored.
_PREFIX_C_BIND = ["bind-key", "-T", "prefix", "c"]

#: What a stock, never-bound tmux server answers prefix+`c` with (confirmed
#: against tmux 3.7c's `list-keys -T prefix c`). tmux has no revert-to-default
#: primitive, so this literal is both `install_window_binding`'s if-shell
#: else-branch and the whole of `reset_window_binding` — the two MUST agree, so
#: they read it from here rather than each spelling it.
_TMUX_DEFAULT_WINDOW_COMMAND = "new-window"


#: The `c` line inside `list-keys -T prefix` output, anchored on the key's
#: POSITION — the operand immediately after `-T prefix` — rather than on the
#: text ` c ` appearing anywhere in the line. tmux's own stock `display-menu`
#: bindings embed bare `c` operands inside their menu definitions, so a text
#: match finds those instead of the window-creation key. The `(?:-\S+\s+)*`
#: run absorbs flags tmux renders between `bind-key` and `-T`, such as the
#: `-r` (repeatable) flag, which it prints as `bind-key -r -T prefix c ...`.
_PREFIX_C_LINE_RE = re.compile(r"^bind-key\s+(?:-\S+\s+)*-T\s+prefix\s+c\s", re.M)


def prefix_window_binding_line(table: str) -> str | None:
    """The whole `c` line from *table* (`list-keys -T prefix` output), as
    tmux itself rendered it, or ``None`` when the key carries no binding.

    Returned verbatim, including tmux's own column padding and quoting,
    because the one thing done with it is handing it straight back to tmux
    through :meth:`Tmux.source_command` — `list-keys` output is written in
    tmux's own command grammar precisely so it can be re-sourced, and camp
    re-tokenizing it would put a second, disagreeing parser in the path.
    """
    for line in table.splitlines():
        if _PREFIX_C_LINE_RE.match(line):
            return line
    return None


def _escape_tmux_format(text: str) -> str:
    """Double every `#` in *text* so tmux renders it literally instead of
    evaluating it as a format expression.

    `##` is tmux's own escape for a literal `#` (confirmed against real tmux
    3.7c: `display-message -p 'a##b'` prints `a#b`, and `'##{E:NAME}'`
    prints the literal `#{E:NAME}` rather than the variable's value). A `#`
    that begins no format expression is unaffected by the round trip, so
    this is safe to apply unconditionally rather than only to text that
    looks like a format.
    """
    return text.replace("#", "##")


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
    """One session tmux reported, as its name and live window count."""

    name: str
    windows: int


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
    r"|no server running on "
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
            return None
        window_id, _, actual_name = _strip_one_trailing_newline(done.stdout).partition(" ")
        return NewWindowResult(window_id=window_id, window_name=actual_name)

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

    def kill_session_with_reason(
        self, name: str, *, timeout: float | None = None
    ) -> tuple[subprocess.CompletedProcess | None, str | None]:
        """Same call :meth:`kill_session` makes, plus the exception's own
        message when the call could not complete at all.

        The consumer is `launch/session.py`'s confirmation-timeout cleanup,
        which reports a failed reclaim on stderr and — unlike
        :meth:`kill_session`'s other, best-effort callers, which discard the
        result entirely — has an operator to tell *why* tmux could not be
        asked, the one thing a plain ``None`` throws away.
        """
        return self._run_with_reason(["kill-session", "-t", target(name)], timeout=timeout)

    def set_environment_with_reason(
        self,
        name: str,
        operand: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[subprocess.CompletedProcess | None, str | None]:
        """Same call :meth:`set_environment` makes, plus the exception's own
        message when the call could not complete at all — the piece of
        information `launch/session.py`'s session-environment statement
        reports on stderr instead of a synthesized "tmux did not answer".
        """
        return self._run_with_reason(
            ["set-environment", "-t", target(name), *operand],
            timeout=timeout,
            env=env,
        )

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
        :func:`target` itself. The two callers of this method address a
        session two different ways: `create_workspace_session` has a session
        NAME and passes ``target(name)``; the window-dispatch verb has only
        the tmux-minted numeric session id (`#{session_id}`, e.g. ``$3``)
        that fired the binding, which :func:`target`'s ``=``-name-prefix
        qualification does not apply to and must not be run through.
        Either way, *target* is passed through :meth:`_pane_syntax_target`
        first — see its docstring for the real-tmux quirk that makes this
        necessary.

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

    def install_window_binding(
        self, true_command: str, *, timeout: float | None = None
    ) -> subprocess.CompletedProcess | None:
        """Install (or re-issue, idempotently) camp's server-global
        prefix+``c`` binding: ``bind-key -T prefix c if-shell -F
        '#{@camp_workspace}' <true_command> 'new-window'``.

        tmux has no revert-to-compiled-default primitive — `bind-key`
        overwrites the single table entry — so the else-branch, literally
        ``new-window``, is what a stock, never-bound tmux server already
        answers prefix+``c`` with (confirmed against tmux 3.7c's
        `list-keys -T prefix c`). Re-issuing this exact argv a second time
        produces a byte-identical `list-keys -T prefix` answer — this
        method itself never decides "first install" vs. "already there";
        see `camp.launch.binding.install_window_key_binding`, the one
        caller, for that.

        *true_command* is the tmux command string run when the current
        session carries a truthy ``@camp_workspace`` option — composed by
        the caller, never built here, since this seam builds tmux ARGV, not
        the command text if-shell's own arguments hold.
        """
        return self._run(
            [
                *_PREFIX_C_BIND,
                "if-shell",
                "-F",
                "#{@camp_workspace}",
                true_command,
                _TMUX_DEFAULT_WINDOW_COMMAND,
            ],
            timeout=timeout,
        )

    def reset_window_binding(
        self, *, timeout: float | None = None
    ) -> subprocess.CompletedProcess | None:
        """Restore camp's server-global prefix+``c`` binding to tmux's own
        compiled-in default: ``bind-key -T prefix c new-window``.

        tmux has no revert-to-default primitive — this reasserts the exact
        else-branch :meth:`install_window_binding` already wraps in
        `if-shell`, literally, with no `if-shell` around it, so the key
        behaves as a stock, never-bound tmux server would regardless of
        which session fires it. Idempotent and server-global (no `-t`):
        issuing this against a server that never had camp's binding
        installed reasserts tmux's own default and still succeeds.

        Returns ``None`` when tmux could not be asked at all; the caller
        decides what to do with a non-zero exit.
        """
        return self._run(
            [*_PREFIX_C_BIND, _TMUX_DEFAULT_WINDOW_COMMAND],
            timeout=timeout,
        )

    def list_window_binding(self, *, timeout: float | None = None) -> str | None:
        """Every binding in the ``prefix`` table (``tmux list-keys -T
        prefix``), or ``None`` when tmux could not answer.

        Deliberately NOT ``list-keys -T prefix c`` — measured on tmux 3.7c,
        a trailing key token there is not a per-key filter (there is no
        such flag on `list-keys`); it produces an EMPTY answer every time,
        first install or not, which silently defeated the
        first-install/re-install distinction until this was caught against
        a real server. The caller (`camp.launch.binding`) searches the
        WHOLE table's text for its own marker rather than isolating the
        `c` line, because `list-keys` re-serializes a `run-shell` string's
        internal quoting (backslash-escaping embedded `"`), so a caller
        comparing the composed command VERBATIM against this output would
        never match — a stable, quote-free substring of the composed
        command survives that round-trip unescaped and is what actually
        gets matched.

        The one way a caller can tell "first install" from "already
        installed" — read this BEFORE calling :meth:`install_window_binding`
        and compare.
        """
        done = self._run(["list-keys", "-T", "prefix"], timeout=timeout)
        if done is None or done.returncode != 0:
            return None
        return done.stdout

    def display_message(
        self, target: str, message: str, *, timeout: float | None = None
    ) -> subprocess.CompletedProcess | None:
        """Show *message* to the client attached to *target* (``tmux
        display-message -t <target> <message>``) — the operator-facing
        surface a detached ``run-shell`` dispatch has for a refusal, since
        it owns no terminal of its own.

        *target* is caller-supplied, not qualified here — see
        :meth:`set_option`'s docstring for why.

        *message* is FORMAT-expanded by tmux before it is shown: verified
        against real tmux 3.7c that `#{E:NAME}` in a message is replaced by
        that variable's value read from the session environment. Camp's
        refusal messages embed operator-influenced text — a workspace slug,
        a group name, a path — and this is the one sink in this seam that
        carries such text as a tmux FORMAT rather than as a `-t` operand or
        an option value, so every `#` is doubled here (`##` is tmux's own
        escape for a literal `#`, confirmed on the same server) before the
        message leaves camp. tmux collapses the escape when it renders, so
        what the operator READS is byte-identical to what the caller wrote;
        what tmux never gets is a format expression it would evaluate.

        Escaping lives HERE rather than at each composing call site for the
        same reason :func:`target` lives here: a sink that is only safe when
        every caller remembers to sanitize is one caller away from not being
        safe, and `window_dispatch` composes these messages at seven
        separate points.
        """
        return self._run(
            ["display-message", "-t", target, _escape_tmux_format(message)],
            timeout=timeout,
        )

    def set_server_option(
        self, key: str, value: str, *, timeout: float | None = None
    ) -> subprocess.CompletedProcess | None:
        """State one SERVER-global user option (``tmux set-option -g <key>
        <value>``).

        The deliberate opposite of :meth:`set_option`'s session-local
        contract, and a separate method rather than a flag on it so neither
        scope can be reached by accident. What this scope is for: a value
        that must outlive the workspace session that wrote it but die with
        the tmux server — which is exactly the lifetime of a server-global
        key binding, and so of the binding camp displaces to install its
        own.
        """
        return self._run(["set-option", "-g", key, value], timeout=timeout)

    def show_server_option(self, key: str, *, timeout: float | None = None) -> str | None:
        """Read back what :meth:`set_server_option` stored, or ``None``.

        ``None`` covers both "never set" and "could not ask": tmux answers a
        non-zero exit with ``invalid option: <key>`` for an unset user option
        (confirmed against real tmux 3.7c), and the caller's decision is the
        same either way — there is nothing captured to act on, so fall back.
        This is the one place in this seam where the two are deliberately
        folded rather than kept apart, because no caller can do anything
        different with them.
        """
        done = self._run(["show-options", "-gv", key], timeout=timeout)
        if done is None or done.returncode != 0:
            return None
        return _strip_one_trailing_newline(done.stdout)

    def unset_server_option(
        self, key: str, *, timeout: float | None = None
    ) -> subprocess.CompletedProcess | None:
        """Drop a server-global user option (``tmux set-option -gu <key>``),
        so a later :meth:`show_server_option` answers ``None`` again."""
        return self._run(["set-option", "-gu", key], timeout=timeout)

    def source_command(
        self, command: str, *, timeout: float | None = None
    ) -> subprocess.CompletedProcess | None:
        """Run one tmux command written in tmux's OWN command grammar, by
        handing it to ``tmux source-file -`` on stdin.

        This exists for exactly one job: replaying a `bind-key` line that
        `list-keys` produced. That output is written to be re-sourceable, so
        tmux's own parser is the only one guaranteed to read it back the way
        it was written — camp splitting the line itself would introduce a
        second parser that can disagree, and tmux's quoting is not POSIX
        shell's. Confirmed against real tmux 3.7c that a captured line
        round-trips byte-for-byte through this path, `-r` flag and embedded
        `#{...}` format included.
        """
        return self._run(["source-file", "-"], timeout=timeout, stdin_text=command + "\n")
