"""The tmux seam: every tmux invocation camp makes, in one place.

Camp talks to tmux through exactly this module. `Tmux` answers the three
tri-state questions the stop engine was built around (`has_session`,
`pane_command`, `list_sessions`) and owns every other tmux invocation camp
performs — spawning a session with an explicit command and a scrubbed
environment (`spawn_session`), starting a bare login-shell window over it
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
    ) -> subprocess.CompletedProcess | None:
        done, _reason = self._run_with_reason(args, timeout=timeout, env=env)
        return done

    def _run_with_reason(
        self,
        args: Sequence[str],
        *,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
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
        env: Mapping[str, str] | None = None,
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
            env=env,
        )
        if done is None:
            return UNANSWERED
        if done.returncode != 0:
            return None
        stdout = done.stdout
        if stdout.endswith("\n"):
            stdout = stdout[:-1]
        window_id, _, actual_name = stdout.partition(" ")
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
        value = done.stdout
        if value.endswith("\n"):
            value = value[:-1]
        return value

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
                "bind-key",
                "-T",
                "prefix",
                "c",
                "if-shell",
                "-F",
                "#{@camp_workspace}",
                true_command,
                "new-window",
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
            ["bind-key", "-T", "prefix", "c", "new-window"],
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
        """
        return self._run(["display-message", "-t", target, message], timeout=timeout)
