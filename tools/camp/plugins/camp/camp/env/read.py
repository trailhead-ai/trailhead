"""Read one machine's environments: locally through Docker, or over ssh through the transport.

``read_local`` and ``read_remote`` each return a :class:`MachineInventory` and never raise
for anything the machine or the network does. ``kind`` is one of

* ``ENVIRONMENTS`` — the reply started with the inventory header and every record parsed;
  ``environments`` holds them (possibly none: an empty machine, not a failure);
* ``DOCKER_NOT_INSTALLED`` — no ``docker`` to run;
* ``DOCKER_NOT_RUNNING`` — ``docker`` could not reach its daemon;
* ``UNREADABLE`` — no believable answer; ``reason`` is one printable line saying why.

``environments`` is empty unless ``kind`` is ``ENVIRONMENTS``; ``reason`` is empty unless it is
``UNREADABLE``.

How a reply is classified
-------------------------
Success is never read from an exit status: an ssh server on a Mac may report 0 whatever the remote
command did. Failure is read from one status only: ssh's own exit 255 (a connection that failed or
dropped mid-reply) is never believed, even when stdout carries the header and records; the reason
is stderr's first line, else ``ssh exited 255 without a reason``. Any other status leaves the reply
to be judged by its content: it is believed when its stdout starts with the inventory header
(``camp.env.contract.parse_inventory`` then decides every record or the whole reply is
unreadable). When stdout lacks the header, stderr names what happened, matched case-insensitively:

* Docker not installed — stderr contains ``not found`` (a POSIX shell) or ``unknown command`` (fish
  prints ``fish: Unknown command: docker``); also a local ``FileNotFoundError`` and, over ssh, the
  transport's exit 127.
* Docker not running — stderr contains ``Cannot connect to the Docker daemon`` or
  ``failed to connect to the docker API`` (a missing socket file prints the second).
* Exit 0 with nothing on stdout or stderr — unreadable, ``empty reply``.
* Anything else — unreadable, carrying stderr's first non-blank line, or the parser's reason when
  stderr says nothing.

The daemon phrases are checked before the not-found ones.

The bound
---------
``READ_TIMEOUT_SECONDS`` bounds a whole read — connection and Docker's answer together. It is read
when a read starts, so changing it takes effect on the next call. A read that exceeds it is
unreadable, ``no answer within <N>s``. A remote reply is read to at most ``MAX_REPLY_BYTES``; a
larger one is unreadable, ``reply too large``.

Transport failures
------------------
Each class maps to its own reason, in the operator's words and with the next step where there is
one: unreachable names the transport's own cause; an unpinned host key says to run ``ssh
<destination> once to pin it``; a changed host key says to check it; refused credentials say so; a
host that stopped responding is the timeout reason. Every reason from far-side text passes through
``camp.printable.printable_line``: one line, printable characters only, at most 200 characters.

``runner`` is injected for tests. Left unset, the reads run the real ``docker`` (locally) or ``ssh``
(remotely), each found through ``PATH`` when the call is made.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Literal

from ..host import transport
from ..host.config import Host
from ..printable import printable_line
from .contract import (
    INVENTORY_ARGV,
    INVENTORY_REMOTE_COMMAND,
    Environment,
    Unreadable,
    has_inventory_header,
    parse_inventory,
)

ENVIRONMENTS = "environments"
DOCKER_NOT_INSTALLED = "docker-not-installed"
DOCKER_NOT_RUNNING = "docker-not-running"
UNREADABLE = "unreadable"

#: How long one machine's read may take, connection included, seconds.
READ_TIMEOUT_SECONDS = 30

#: The most stdout a read takes from a machine, bytes.
MAX_REPLY_BYTES = 1024 * 1024

_LINE_BREAK = re.compile(r"[\r\n]+")
_NOT_INSTALLED_PHRASES = ("not found", "unknown command")
_NOT_RUNNING_PHRASES = ("cannot connect to the docker daemon", "failed to connect to the docker api")


@dataclass(frozen=True)
class MachineInventory:
    kind: Literal["environments", "docker-not-installed", "docker-not-running", "unreadable"]
    environments: tuple[Environment, ...] = ()
    reason: str = ""


def _unreadable(reason: str) -> MachineInventory:
    return MachineInventory(UNREADABLE, reason=printable_line(reason).strip())


def _timeout() -> MachineInventory:
    return _unreadable(f"no answer within {READ_TIMEOUT_SECONDS:g}s")


def _first_line(text: str) -> str:
    for line in _LINE_BREAK.split(text):
        if printable_line(line).strip():
            return line
    return ""


def _from_reply(stdout: str, stderr: str, exit_code: int) -> MachineInventory:
    if has_inventory_header(stdout):
        try:
            return MachineInventory(ENVIRONMENTS, tuple(parse_inventory(stdout)))
        except Unreadable as error:
            return _unreadable(str(error))
    lowered = stderr.lower()
    if any(phrase in lowered for phrase in _NOT_RUNNING_PHRASES):
        return MachineInventory(DOCKER_NOT_RUNNING)
    if any(phrase in lowered for phrase in _NOT_INSTALLED_PHRASES):
        return MachineInventory(DOCKER_NOT_INSTALLED)
    line = _first_line(stderr)
    if line:
        return _unreadable(line)
    if not stdout.strip():
        if exit_code == 0:
            return _unreadable("empty reply")
        return _unreadable(f"docker exited {exit_code} without a reason")
    try:
        parse_inventory(stdout)
    except Unreadable as error:
        return _unreadable(str(error))
    return _unreadable("reply not understood")


def read_local(*, runner: transport.BoundedRunner = transport.bounded_runner) -> MachineInventory:
    """Read this machine's environments by running Docker here."""
    env = {**os.environ, "LC_ALL": "C"}
    try:
        result = runner(list(INVENTORY_ARGV), READ_TIMEOUT_SECONDS, env, MAX_REPLY_BYTES)
    except FileNotFoundError:
        return MachineInventory(DOCKER_NOT_INSTALLED)
    except subprocess.TimeoutExpired:
        return _timeout()
    except OSError as error:
        return _unreadable(f"cannot run docker: {error.strerror or error}")
    if len(result.stdout.encode("utf-8", errors="surrogateescape")) > MAX_REPLY_BYTES:
        return _unreadable("reply too large")
    return _from_reply(result.stdout, result.stderr, result.exit_code)


def read_remote(
    name: str,
    host: Host,
    *,
    connect_timeout: float,
    runner: transport.BoundedRunner = transport.bounded_runner,
) -> MachineInventory:
    """Read host ``name``'s environments over ssh. ``connect_timeout`` is ssh's handshake bound."""
    outcome = transport.run_remote_command(
        host,
        INVENTORY_REMOTE_COMMAND,
        connect_timeout=connect_timeout,
        execution_timeout=READ_TIMEOUT_SECONDS,
        max_stdout_bytes=MAX_REPLY_BYTES,
        runner=runner,
    )
    destination = host.ssh
    if isinstance(outcome, (transport.Answered, transport.RemoteRefusal)):
        if outcome.exit_code == 255:
            return _unreadable(_first_line(outcome.stderr) or "ssh exited 255 without a reason")
        return _from_reply(outcome.stdout, outcome.stderr, outcome.exit_code)
    if isinstance(outcome, transport.CampNotResolvable):
        return MachineInventory(DOCKER_NOT_INSTALLED)
    if isinstance(outcome, transport.Unreachable):
        return _unreadable(f"unreachable ({outcome.reason})")
    if isinstance(outcome, transport.IdentityUnknown):
        return _unreadable(f"the host key is not pinned; run ssh {destination} once to pin it")
    if isinstance(outcome, transport.IdentityChanged):
        return _unreadable(
            f"the host key for {destination} has changed; check it before connecting again"
        )
    if isinstance(outcome, transport.CredentialsRefused):
        return _unreadable("ssh credentials were refused")
    if isinstance(outcome, transport.StoppedResponding):
        return _timeout()
    if isinstance(outcome, transport.TooLarge):
        return _unreadable("reply too large")
    return _unreadable(transport.outcome_detail(outcome))
