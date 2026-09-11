"""The SSH transport: one camp invocation on one declared host, classified.

Builds a single ``ssh`` argv with ``BatchMode=yes``, ``StrictHostKeyChecking=yes``,
and a ``ConnectTimeout``, assembles the remote command by quoting every element
individually so no far-side shell can interpret any character in it, and
classifies the result into a closed set of outcomes rather than raising a raw
transport error to its caller.

Two bounds. ``connect_timeout`` bounds the handshake, passed straight through
as ssh's own ``ConnectTimeout``. ``execution_timeout`` bounds the whole
invocation on the local side, so a host that connects and then wedges is a
stated outcome (:class:`StoppedResponding`) rather than an indefinite hang.

Classification runs on the exit code and the message together, under a fixed
``LC_ALL=C`` applied to the child's environment regardless of the caller's own
locale, because several outcomes share exit code 255 and the messages that
separate them are locale-dependent.

Resolution order, measured 2026-09-10 against real ``ssh``: a remote camp
invocation exiting 255 is indistinguishable from a transport failure by exit
code alone, and may print nothing. The fixed transport substrings are
therefore matched FIRST — unreachable, then the two host-identity states,
then the authentication-failure signal (:class:`CredentialsRefused`) — and an
otherwise-unmatched 255 is the remote command's own exit, classified as
:class:`RemoteRefusal`. Never the reverse — assuming transport first would
render a genuine remote refusal as a connection failure that never happened.
:class:`CredentialsRefused` covers the single most likely first-contact
failure: ``BatchMode=yes`` with no usable identity loaded produces ``ssh``'s
own ``Permission denied`` line, never a password prompt.

Residual, also measured: terminating the local ``ssh`` child (on the execution
timeout, or on an interrupt unwinding through this call) bounds only the local
side. The remote command is reparented and is not terminated by it — an
interrupted or timed-out invocation may leave a remote camp process running
until it exits on its own. A pty would close that gap by delivering SIGHUP on
channel close, but is not taken here — not, as this module once claimed,
because a pty would mangle the byte-verbatim stream: CRLF translation is not
exclusive to a pty. CPython's own text-mode ``Popen`` (:func:`default_runner`,
via ``encoding="utf-8"``) applies universal-newlines translation on its own,
with no pty and no ssh anywhere — measured 2026-09-11, a bare CR rewritten to
LF locally, before this module ever saw it. :func:`default_runner` is
therefore unusable for a binary-verbatim payload with or without a pty, and it
also passes no ``stdin=PIPE`` at all — the :data:`Runner` signature carries no
input parameter, so it cannot feed a producer regardless of buffering.

A second channel, :func:`stream_camp`, exists for exactly that payload: one
local producer's stdout, piped verbatim and binary into one remote camp
invocation's stdin. It opens its own ``subprocess.Popen`` in binary mode —
no ``encoding=``, no ``text=True`` — and bypasses :func:`default_runner`
entirely, reusing only :func:`quote_and_join` and the fixed ssh options, and
:func:`_classify` for outcome classification, so a peer failure is never
classified two different ways depending on which channel saw it. It carries
no wall-clock execution bound — the bound is the producer exiting — so it adds
``ServerAliveInterval``/``ServerAliveCountMax`` among its ssh options: without
them a socket that dies without closing (a sleeping laptop, an expired NAT
entry, a silent partition) hangs both ends for as long as the operator is
willing to wait. Detection of that dead socket is itself bounded locally,
derived from those same keepalive settings, rather than relying solely on
ssh's own server-side detection.

Security: the assembled remote command carries slugs, group names, session
references, and the host's camp location. Nothing in this module logs it —
readable by other local users on a multi-user machine.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from .config import Host

#: ssh's own handshake bound, seconds. Operator-facing default; override via
#: the ``connect_timeout`` parameter.
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0

#: The local-side bound on the whole invocation, seconds — connect plus
#: however long the remote camp takes to answer. Operator-facing default;
#: override via the ``execution_timeout`` parameter.
DEFAULT_EXECUTION_TIMEOUT_SECONDS = 60.0

#: ssh's own keepalive probe interval, seconds — :func:`stream_camp`'s
#: ``ServerAliveInterval``. Operator-facing default; override via the
#: ``server_alive_interval`` parameter.
DEFAULT_SERVER_ALIVE_INTERVAL_SECONDS = 15.0

#: How many unanswered keepalive probes ssh tolerates before it gives up —
#: :func:`stream_camp`'s ``ServerAliveCountMax``. Operator-facing default;
#: override via the ``server_alive_count_max`` parameter.
DEFAULT_SERVER_ALIVE_COUNT_MAX = 3

# Transport-level failure substrings, measured against real ssh on
# 2026-09-10 under LC_ALL=C. All three share exit code 255 with every other
# 255 outcome below, so the message is the only signal.
_DNS_FAILURE = "Could not resolve hostname"
_CONNECTION_REFUSED = "Connection refused"
_CONNECT_TIMEOUT = "Connection timed out"
_NO_ROUTE_TO_HOST = "No route to host"
_NETWORK_UNREACHABLE = "Network is unreachable"
# macOS strerror(ETIMEDOUT) — distinct wording from "Connection timed out"
# above, which is the Linux/glibc form. Both are live: the operator runs two
# Macs, so this is not a hypothetical.
_DARWIN_OPERATION_TIMED_OUT = "Operation timed out"
_UNREACHABLE_SUBSTRINGS = (
    _DNS_FAILURE,
    _CONNECTION_REFUSED,
    _CONNECT_TIMEOUT,
    _NO_ROUTE_TO_HOST,
    _NETWORK_UNREACHABLE,
    _DARWIN_OPERATION_TIMED_OUT,
)

_IDENTITY_CHANGED = "REMOTE HOST IDENTIFICATION HAS CHANGED"

# Never a literal key algorithm name — the message interpolates it
# ("No ED25519 host key is known for ..."), so matching only the
# algorithm-independent halves stays correct for any key type ssh offers.
_IDENTITY_UNKNOWN_A = "host key is known for"
_IDENTITY_UNKNOWN_B = "requested strict checking"

# ssh's own authentication-failure wording, measured 2026-09-10 against real
# ssh under LC_ALL=C with BatchMode=yes and no usable identity loaded:
# "<user>@<host>: Permission denied (publickey)." (and, with more offered
# methods, "Permission denied (publickey,password)."). Never the username or
# the parenthesized method list — both vary by target and by what the local
# ssh-agent offers, so only the invariant "Permission denied" is matched.
_PERMISSION_DENIED = "Permission denied"


@dataclass(frozen=True)
class TransportOutcome:
    """Base of the closed outcome set. Never returned itself."""


@dataclass(frozen=True)
class Answered(TransportOutcome):
    """The remote camp invocation completed. ``exit_code`` is its own, verbatim."""

    stdout: str
    stderr: str
    exit_code: int


@dataclass(frozen=True)
class Unreachable(TransportOutcome):
    """The connection did not complete — DNS failure, refusal, or connect timeout."""

    reason: str


@dataclass(frozen=True)
class StoppedResponding(TransportOutcome):
    """The connection completed and the invocation then exceeded its execution bound."""

    execution_timeout: float


@dataclass(frozen=True)
class IdentityUnknown(TransportOutcome):
    """The host has no pinned key. Routine first contact — camp will not pin one."""


@dataclass(frozen=True)
class IdentityChanged(TransportOutcome):
    """The host presented a key that is not the pinned one."""


@dataclass(frozen=True)
class CampNotResolvable(TransportOutcome):
    """The connection completed but the far side could not run camp."""


@dataclass(frozen=True)
class CredentialsRefused(TransportOutcome):
    """ssh authenticated with nothing — the remote host refused every
    credential offered. The connection never completed and camp never ran;
    the remedy is entirely on the operator's side (load an identity, get it
    authorized on the far side)."""


@dataclass(frozen=True)
class RemoteRefusal(TransportOutcome):
    """The remote camp ran and refused. Its own stderr, carried through unchanged."""

    stdout: str
    stderr: str
    exit_code: int


@dataclass(frozen=True)
class ProducerFailed(TransportOutcome):
    """:func:`stream_camp`'s local producer exited non-zero before the stream
    completed. The remote invocation is aborted rather than being left to
    receive a truncated stream and answer as though nothing were wrong."""

    exit_code: int


@dataclass(frozen=True)
class RawResult:
    """What a :data:`Runner` hands back from one completed invocation."""

    stdout: str
    stderr: str
    exit_code: int


#: The injected seam, mirroring the ``Tmux`` seam at launch/stop.py:158 — a
#: callable rather than a class, since there is exactly one operation. Raises
#: ``subprocess.TimeoutExpired`` to signal that the invocation exceeded
#: ``execution_timeout`` without answering; any other exception is not part of
#: this seam's contract and propagates to the caller.
Runner = Callable[[Sequence[str], float, Mapping[str, str]], RawResult]


def default_runner(
    argv: Sequence[str], execution_timeout: float, env: Mapping[str, str]
) -> RawResult:
    """Actually run ``argv``, bounded by ``execution_timeout``.

    On timeout, or on any exception unwinding through this call (an interrupt
    included), the child is killed before the exception propagates — the
    local side only; see the module docstring's residual.
    """
    process = subprocess.Popen(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="surrogateescape",
        env=dict(env),
    )
    try:
        stdout, stderr = process.communicate(timeout=execution_timeout)
    except BaseException:
        # Covers the execution-timeout expiry and every other unwind (an
        # interrupt included) with one handler — the cleanup is the same.
        _kill(process)
        raise
    return RawResult(stdout=stdout, stderr=stderr, exit_code=process.returncode)


def _kill(process: subprocess.Popen) -> None:
    process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def quote_and_join(camp_bin: str, remote_argv: Sequence[str]) -> str:
    """Quote ``camp_bin`` and every element of ``remote_argv`` individually,
    then join with a single space — the exact remote-command shape ``ssh``
    hands to the far side's login shell to parse and execute as one line.

    Shared by every path that assembles a remote camp invocation (this
    module's own :func:`run_camp`, and the interactive handoff in
    ``host/handoff.py``) so there is exactly one place that decides how a
    remote argv is quoted, never a second implementation of it.
    """
    return " ".join(shlex.quote(part) for part in (camp_bin, *remote_argv))


def _fixed_ssh_options(connect_timeout: float) -> list[str]:
    """The three fixed ``-o`` pairs every remote camp invocation carries —
    shared by :func:`run_camp` and :func:`stream_camp` so there is exactly
    one place that decides them, never a second assembly of the same three
    options."""
    return [
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"ConnectTimeout={connect_timeout:g}",
    ]


def run_camp(
    host: Host,
    remote_argv: Sequence[str],
    *,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    execution_timeout: float = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    runner: Runner = default_runner,
    extra_ssh_options: Sequence[str] = (),
) -> TransportOutcome:
    """Run ``camp <remote_argv...>`` on ``host`` and classify the outcome.

    ``extra_ssh_options`` are additional ``-o key=value`` pairs appended after
    the three fixed ones (``BatchMode``, ``StrictHostKeyChecking``,
    ``ConnectTimeout``) — the seam tests use to point ``UserKnownHostsFile`` at
    a throwaway file; production callers pass none.
    """
    remote_command = quote_and_join(host.camp_bin, remote_argv)
    ssh_argv: list[str] = ["ssh", *_fixed_ssh_options(connect_timeout)]
    for option in extra_ssh_options:
        ssh_argv += ["-o", option]
    ssh_argv += [host.ssh, remote_command]

    env = {**os.environ, "LC_ALL": "C"}

    try:
        raw = runner(ssh_argv, execution_timeout, env)
    except subprocess.TimeoutExpired:
        return StoppedResponding(execution_timeout=execution_timeout)

    return _classify(raw)


def _classify(raw: RawResult) -> TransportOutcome:
    if raw.exit_code == 0:
        return Answered(stdout=raw.stdout, stderr=raw.stderr, exit_code=raw.exit_code)

    if raw.exit_code == 255:
        for substring in _UNREACHABLE_SUBSTRINGS:
            if substring in raw.stderr:
                return Unreachable(reason=substring)
        if _IDENTITY_CHANGED in raw.stderr:
            return IdentityChanged()
        if _IDENTITY_UNKNOWN_A in raw.stderr and _IDENTITY_UNKNOWN_B in raw.stderr:
            return IdentityUnknown()
        if _PERMISSION_DENIED in raw.stderr:
            return CredentialsRefused()
        return RemoteRefusal(stdout=raw.stdout, stderr=raw.stderr, exit_code=raw.exit_code)

    if raw.exit_code == 127:
        # Unambiguous against every transport failure (all of which are
        # 255), so the message is not required — only the shell's wording
        # for "could not run this" varies (bash: "command not found" for a
        # missing bare command, "No such file or directory" for a wrong
        # camp_bin path; dash/ash: "not found" for both).
        return CampNotResolvable()

    return RemoteRefusal(stdout=raw.stdout, stderr=raw.stderr, exit_code=raw.exit_code)


# -----------------------------------------------------------------------
# stream_camp — the binary streaming sibling of run_camp.
# -----------------------------------------------------------------------

#: The injected seam for :func:`stream_camp` — a callable rather than the
#: ``Runner`` seam above, because this channel needs the live child process
#: (its ``stdin``/``stdout``/``stderr`` pipes and ``poll()``) to pump a
#: producer's bytes into it while it runs, not a single buffered result.
StreamSpawner = Callable[[Sequence[str], Mapping[str, str]], "subprocess.Popen[bytes]"]


def default_stream_spawner(
    argv: Sequence[str], env: Mapping[str, str]
) -> "subprocess.Popen[bytes]":
    """Spawn ``argv`` in binary mode — no ``encoding=``, no ``text=True`` —
    with its stdin, stdout, and stderr all piped, so :func:`stream_camp` can
    feed it a producer's bytes and drain its output concurrently."""
    return subprocess.Popen(
        list(argv),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
    )


#: The injected seam for the local producer :func:`stream_camp` reads from —
#: argv in, a running ``Popen[bytes]`` with ``stdout=PIPE`` out — so a caller
#: assembling a producer can substitute a recording fake without touching
#: :data:`StreamSpawner`, the separate seam for the ssh child itself.
ProducerSpawner = Callable[[Sequence[str]], "subprocess.Popen[bytes]"]


def default_producer_spawn(argv: Sequence[str]) -> "subprocess.Popen[bytes]":
    """Spawn *argv* with only stdout piped — the shape :func:`stream_camp`
    requires of a producer it will read from and classify on exit."""
    return subprocess.Popen(list(argv), stdout=subprocess.PIPE)


def stream_camp(
    host: Host,
    remote_argv: Sequence[str],
    producer: "subprocess.Popen[bytes]",
    *,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    server_alive_interval: float = DEFAULT_SERVER_ALIVE_INTERVAL_SECONDS,
    server_alive_count_max: int = DEFAULT_SERVER_ALIVE_COUNT_MAX,
    extra_ssh_options: Sequence[str] = (),
    spawn: StreamSpawner = default_stream_spawner,
) -> TransportOutcome:
    """Run ``camp <remote_argv...>`` on ``host``, piping ``producer``'s stdout
    into its stdin verbatim and binary, and classify the outcome.

    ``producer`` must already be running with ``stdout=subprocess.PIPE``. Its
    bytes are copied into the remote invocation's stdin exactly as produced —
    no text encoding, no newline translation — until ``producer`` closes its
    stdout. If ``producer`` then exits non-zero, the invocation is failed as
    :class:`ProducerFailed` naming its exit code, rather than letting a
    truncated stream reach the remote command and be answered as though
    nothing were wrong.

    There is no wall-clock execution bound — this channel is expected to run
    for minutes, bounded only by ``producer`` exiting. A connection that dies
    without closing is instead caught by a local bound derived from
    ``server_alive_interval`` and ``server_alive_count_max``
    (``server_alive_interval * server_alive_count_max + connect_timeout``):
    if the remote invocation has not completed within that bound, it is
    killed and classified as :class:`StoppedResponding`.

    Reuses :func:`quote_and_join`, :func:`_fixed_ssh_options`, and
    :func:`_classify` — the same assembly and the same classifier
    :func:`run_camp` uses, so a peer failure is never classified two
    different ways depending on which channel observed it.
    """
    remote_command = quote_and_join(host.camp_bin, remote_argv)
    ssh_argv: list[str] = [
        "ssh",
        *_fixed_ssh_options(connect_timeout),
        "-o", f"ServerAliveInterval={server_alive_interval:g}",
        "-o", f"ServerAliveCountMax={server_alive_count_max}",
    ]
    for option in extra_ssh_options:
        ssh_argv += ["-o", option]
    ssh_argv += [host.ssh, remote_command]

    env = {**os.environ, "LC_ALL": "C"}

    child = spawn(ssh_argv, env)

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    def _feed() -> None:
        try:
            assert producer.stdout is not None
            shutil.copyfileobj(producer.stdout, child.stdin)
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                child.stdin.close()
            except OSError:
                pass

    def _drain(source, sink: list[bytes]) -> None:
        for chunk in iter(lambda: source.read(65536), b""):
            sink.append(chunk)

    feeder = threading.Thread(target=_feed, daemon=True)
    stdout_reader = threading.Thread(target=_drain, args=(child.stdout, stdout_chunks), daemon=True)
    stderr_reader = threading.Thread(target=_drain, args=(child.stderr, stderr_chunks), daemon=True)
    feeder.start()
    stdout_reader.start()
    stderr_reader.start()

    bound = server_alive_interval * server_alive_count_max + connect_timeout
    deadline = time.monotonic() + bound
    child_done = False
    while time.monotonic() < deadline:
        if child.poll() is not None:
            child_done = True
            break
        time.sleep(0.02)

    if not child_done:
        _kill(child)
        feeder.join(timeout=5)
        stdout_reader.join(timeout=5)
        stderr_reader.join(timeout=5)
        try:
            producer.kill()
            producer.wait(timeout=5)
        except Exception:
            pass
        return StoppedResponding(execution_timeout=bound)

    feeder.join()
    stdout_reader.join()
    stderr_reader.join()

    producer_exit = producer.wait()
    if producer_exit != 0:
        return ProducerFailed(exit_code=producer_exit)

    stdout = b"".join(stdout_chunks).decode("utf-8", errors="surrogateescape")
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="surrogateescape")
    raw = RawResult(stdout=stdout, stderr=stderr, exit_code=child.returncode)
    return _classify(raw)
