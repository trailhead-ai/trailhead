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
channel close, and would also apply CRLF translation to the byte-verbatim
``--json`` stream this transport exists to relay, so it is not taken here.

Security: the assembled remote command carries slugs, group names, session
references, and the host's camp location. Nothing in this module logs it —
readable by other local users on a multi-user machine.
"""
from __future__ import annotations

import os
import shlex
import subprocess
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

# Transport-level failure substrings, measured against real ssh on
# 2026-09-10 under LC_ALL=C. All three share exit code 255 with every other
# 255 outcome below, so the message is the only signal.
_DNS_FAILURE = "Could not resolve hostname"
_CONNECTION_REFUSED = "Connection refused"
_CONNECT_TIMEOUT = "Connection timed out"
_UNREACHABLE_SUBSTRINGS = (_DNS_FAILURE, _CONNECTION_REFUSED, _CONNECT_TIMEOUT)

_IDENTITY_CHANGED = "REMOTE HOST IDENTIFICATION HAS CHANGED"

# Never a literal key algorithm name — the message interpolates it
# ("No ED25519 host key is known for ..."), so matching only the
# algorithm-independent halves stays correct for any key type ssh offers.
_IDENTITY_UNKNOWN_A = "host key is known for"
_IDENTITY_UNKNOWN_B = "requested strict checking"

# The remote shell's own "command not found" wording, measured through ssh
# with exit 127. Generic across shells — never a specific shell's prefix.
_COMMAND_NOT_FOUND = "command not found"

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
        text=True,
        env=dict(env),
    )
    try:
        stdout, stderr = process.communicate(timeout=execution_timeout)
    except subprocess.TimeoutExpired:
        _kill(process)
        raise
    except BaseException:
        _kill(process)
        raise
    return RawResult(stdout=stdout, stderr=stderr, exit_code=process.returncode)


def _kill(process: subprocess.Popen) -> None:
    process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


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
    remote_command = " ".join(
        shlex.quote(part) for part in (host.camp_bin, *remote_argv)
    )
    ssh_argv: list[str] = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"ConnectTimeout={connect_timeout:g}",
    ]
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

    if raw.exit_code == 127 and _COMMAND_NOT_FOUND in raw.stderr:
        return CampNotResolvable()

    return RemoteRefusal(stdout=raw.stdout, stderr=raw.stderr, exit_code=raw.exit_code)
