"""An attachment: one background ``ssh`` forward, found again from the process table.

Nothing about an attachment is written to disk. The process table is the only record.
A process is **recognised** as an attachment when all of these hold:

- it belongs to this user (``ps`` reports the numeric uid);
- its argv0 has the basename ``ssh`` (bare or a full path);
- it carries the word ``-N``;
- it carries exactly one ``-o Tag=<ns>:<id>:<host>/<group>/<name>`` option (also
  spelled ``-oTag=...``), where ``<ns>`` is the tag namespace and ``<id>`` the
  attach id, ``<host>`` matches ``[A-Za-z0-9][A-Za-z0-9._-]*`` and group, name and
  id match camp's identifier rule ``[a-z0-9][a-z0-9-]*``. Only words before a
  ``--`` are options: what follows is the destination and any remote command;
- every ``-L 127.0.0.1:<standard>:127.0.0.1:<published>`` argument has ASCII-digit
  ports. A process whose ``-L`` has anything else is not recognised.

The decoys this rejects: another user's forward; a ``grep``/``tail`` that merely
names the tag; an ssh without ``-N`` (an interactive session); an ssh whose tag is
a word of a remote command rather than an ``-o`` value; two tag options; another
namespace; a malformed host, group or name.

``CAMP_ENV_TAG_NAMESPACE`` replaces the namespace ``camp-env`` (the test seam). It
is read on every call, and a value outside camp's identifier rule is refused.
"""
from __future__ import annotations

import errno
import os
import re
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from ..printable import printable_line

DEFAULT_NAMESPACE = "camp-env"
NAMESPACE_ENV = "CAMP_ENV_TAG_NAMESPACE"

#: Seconds ``end`` waits after SIGTERM before it escalates to SIGKILL.
KILL_GRACE_SECONDS = 2.0

UNSEEN_HOLDER = "a process camp cannot see"

_IDENTIFIER_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
_HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_FORWARD_RE = re.compile(r"127\.0\.0\.1:([0-9]+):127\.0\.0\.1:([0-9]+)")
_PS_ARGV = ["ps", "-axwwo", "pid=,uid=,command="]
_POLL_SECONDS = 0.05


class Unverifiable(Exception):
    """The process table could not be read; its message is the printable reason."""


class Untaggable(ValueError):
    """A host, group, name, id or namespace camp refuses to put in a tag."""


@dataclass(frozen=True)
class Attachment:
    """One recognised forward. ``ports`` holds ``(standard, published)`` pairs
    read from its ``-L`` arguments, ordered by standard port."""

    pid: int
    host: str
    group: str
    name: str
    ports: tuple[tuple[int, int], ...]
    attach_id: str


def _namespace() -> str:
    namespace = os.environ.get(NAMESPACE_ENV, DEFAULT_NAMESPACE)
    if _IDENTIFIER_RE.fullmatch(namespace) is None:
        raise Untaggable(f"{NAMESPACE_ENV} is not a valid tag namespace: {printable_line(namespace)!r}")
    return namespace


def tag_for(host: str, group: str, name: str, attach_id: str) -> str:
    """The tag value ``<ns>:<id>:<host>/<group>/<name>`` for one attachment.

    The namespace is ``camp-env`` unless ``CAMP_ENV_TAG_NAMESPACE`` is set, read at
    call time. Raises :class:`Untaggable` for a namespace, host, group, name or id
    outside the rules above (a host with a space or a leading ``-``, for one).
    """
    namespace = _namespace()
    if _HOST_RE.fullmatch(host) is None:
        raise Untaggable(f"host {printable_line(host)!r} cannot be tagged")
    for label, value in (("group", group), ("name", name), ("attach id", attach_id)):
        if _IDENTIFIER_RE.fullmatch(value) is None:
            raise Untaggable(f"{label} {printable_line(value)!r} cannot be tagged")
    return f"{namespace}:{attach_id}:{host}/{group}/{name}"


def _default_ps(argv: Sequence[str], timeout: float) -> str:
    completed = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
    if completed.returncode != 0:
        detail = printable_line(completed.stderr.strip()) or "no message"
        raise Unverifiable(f"ps exited {completed.returncode}: {detail}")
    return completed.stdout


def _read_table(runner: Callable[[list[str]], str] | None, timeout: float) -> str:
    try:
        if runner is None:
            return _default_ps(_PS_ARGV, timeout)
        return runner(list(_PS_ARGV))
    except Unverifiable:
        raise
    except subprocess.TimeoutExpired:
        raise Unverifiable(f"ps did not answer within {timeout:g}s") from None
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise Unverifiable(f"ps could not be run: {printable_line(str(error))}") from None


def _parse_tag(value: str, namespace: str) -> tuple[str, str, str, str] | None:
    prefix = namespace + ":"
    if not value.startswith(prefix):
        return None
    attach_id, separator, rest = value[len(prefix):].partition(":")
    if not separator:
        return None
    parts = rest.split("/")
    if len(parts) != 3:
        return None
    host, group, name = parts
    if _HOST_RE.fullmatch(host) is None:
        return None
    if not all(_IDENTIFIER_RE.fullmatch(v) for v in (attach_id, group, name)):
        return None
    return attach_id, host, group, name


def _recognise(pid: int, command: str, namespace: str) -> Attachment | None:
    words = command.split()
    if not words or os.path.basename(words[0]) != "ssh":
        return None
    if "--" in words:
        words = words[: words.index("--")]
    if "-N" not in words:
        return None
    tags: list[str] = []
    specs: list[str] = []
    index = 1
    while index < len(words):
        word = words[index]
        if word in ("-o", "-L") and index + 1 < len(words):
            value = words[index + 1]
            index += 2
        elif word.startswith(("-o", "-L")) and len(word) > 2:
            value = word[2:]
            index += 1
        else:
            index += 1
            continue
        if word.startswith("-o"):
            if value.startswith("Tag="):
                tags.append(value[len("Tag="):])
        else:
            specs.append(value)
    if len(tags) != 1:
        return None
    parsed = _parse_tag(tags[0], namespace)
    if parsed is None:
        return None
    ports: list[tuple[int, int]] = []
    for spec in specs:
        match = _FORWARD_RE.fullmatch(spec)
        if match is None:
            return None
        ports.append((int(match.group(1)), int(match.group(2))))
    attach_id, host, group, name = parsed
    return Attachment(pid, host, group, name, tuple(sorted(ports)), attach_id)


def attachments(
    *,
    runner: Callable[[list[str]], str] | None = None,
    uid: int | None = None,
    timeout: float = 10.0,
) -> list[Attachment]:
    """Every attachment in the process table, in table order.

    *runner* receives the ``ps -axwwo pid=,uid=,command=`` argv and returns its
    stdout text (the seam for a fake table); the default runs the real ``ps``
    bounded by *timeout*. *uid* defaults to this process's own. Raises
    :class:`Unverifiable` — with a printable reason — when ``ps`` cannot be run,
    times out, exits non-zero or cannot be read, and :class:`Untaggable` when
    ``CAMP_ENV_TAG_NAMESPACE`` is not a valid namespace. A row that does not split
    into pid, uid and command is skipped.
    """
    namespace = _namespace()
    own_uid = os.getuid() if uid is None else uid
    found: list[Attachment] = []
    for line in _read_table(runner, timeout).splitlines():
        fields = line.split(None, 2)
        if len(fields) != 3:
            continue
        pid_text, uid_text, command = fields
        if not (pid_text.isascii() and pid_text.isdigit() and uid_text.isascii() and uid_text.isdigit()):
            continue
        if int(uid_text) != own_uid:
            continue
        recognised = _recognise(int(pid_text), command, namespace)
        if recognised is not None:
            found.append(recognised)
    return found


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _still_attachments(
    wanted: dict[int, str], runner: Callable[[list[str]], str] | None, uid: int | None
) -> set[int]:
    current = {a.pid: a.attach_id for a in attachments(runner=runner, uid=uid)}
    return {pid for pid, attach_id in wanted.items() if current.get(pid) == attach_id}


def _signal(pids: set[int], sig: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def end(
    found: Sequence[Attachment],
    *,
    runner: Callable[[list[str]], str] | None = None,
    uid: int | None = None,
    grace: float = KILL_GRACE_SECONDS,
) -> list[int]:
    """End the given attachments: SIGTERM, then SIGKILL after *grace* seconds.

    Immediately before each signal, SIGTERM and SIGKILL alike, the process table is
    read again and a pid is signalled only when it is still a recognised attachment
    carrying the same attach id — so a pid reused by another process, or by another
    attachment, is never signalled. Returns the pids that are still recognised
    attachments after the attempt (empty when everything ended). Raises
    :class:`Unverifiable` when the table cannot be read; nothing is signalled then.
    """
    wanted = {a.pid: a.attach_id for a in found}
    if not wanted:
        return []
    terminated = _still_attachments(wanted, runner, uid)
    _signal(terminated, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and any(_alive(pid) for pid in terminated):
        time.sleep(_POLL_SECONDS)
    survivors = {pid for pid in terminated if _alive(pid)}
    if survivors:
        _signal(_still_attachments({p: wanted[p] for p in survivors}, runner, uid), signal.SIGKILL)
        settle = time.monotonic() + 1.0
        while time.monotonic() < settle and any(_alive(pid) for pid in survivors):
            time.sleep(_POLL_SECONDS)
    remaining = _still_attachments({p: wanted[p] for p in terminated}, runner, uid)
    return sorted(pid for pid in remaining if _alive(pid))


def _default_bind(family: int, address: str, port: int) -> None:
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.bind((address, port))


def _default_lsof(argv: list[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True, timeout=5).stdout


def _held(bind: Callable[[int, str, int], None], family: int, address: str, port: int) -> bool:
    try:
        bind(family, address, port)
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            return True
        if family == socket.AF_INET6 and error.errno in (errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT):
            return False
        raise
    return False


def _describe(lsof_runner: Callable[[list[str]], str], port: int) -> str:
    argv = ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-F", "pcn"]
    try:
        output = lsof_runner(argv)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return UNSEEN_HOLDER
    pid = command = None
    for line in output.splitlines():
        if line.startswith("p") and pid is None:
            pid = line[1:]
        elif line.startswith("c") and pid is not None and command is None:
            command = printable_line(line[1:])
    if pid is None or not command:
        return UNSEEN_HOLDER
    return f"{command} (pid {printable_line(pid)})"


def port_holder(
    port: int,
    *,
    bind: Callable[[int, str, int], None] | None = None,
    lsof_runner: Callable[[list[str]], str] | None = None,
) -> str | None:
    """None when *port* can be bound on both 127.0.0.1 and ::1, else who holds it.

    camp binds each loopback address itself, without SO_REUSEADDR, and releases it at
    once. Only "address in use" counts as held; an ::1 bind failing because the
    machine has no IPv6 loopback (``EADDRNOTAVAIL``, ``EAFNOSUPPORT``) counts as free
    on ::1. A held port is described as ``<command> (pid <pid>)`` from
    ``lsof -F pcn`` output (the command passed through ``printable_line``), or as
    "a process camp cannot see" when lsof is absent or does not see it. *bind*
    (family, address, port) and *lsof_runner* (argv -> stdout) are test seams.
    """
    binder = bind or _default_bind
    if not (
        _held(binder, socket.AF_INET, "127.0.0.1", port)
        or _held(binder, socket.AF_INET6, "::1", port)
    ):
        return None
    return _describe(lsof_runner or _default_lsof, port)
