"""An attachment: one background ``ssh`` forward, found again from the process table.

Nothing about an attachment is written to disk. The process table is the only record.
A process is **recognised** as an attachment when all of these hold:

- it belongs to this user (``ps`` reports the numeric uid);
- its argv0 has the basename ``ssh`` (bare or a full path);
- its argv parses the way OpenSSH reads its leading options, and stops at the first word that is
  not an option (the destination) or at a ``--``; everything after is never read as an option.
  This is deliberately stricter than OpenSSH, which resumes option parsing after the destination
  unless a ``--`` came first (``ssh -G host -o Tag=x`` prints ``tag x``): camp's own forwards
  always carry ``--``, so they are unaffected, and a hand-built forward with options after its
  destination is not recognised. A letter that takes an argument (``B b c D E e F I i J L l m O o P p Q R S W w``)
  consumes the rest of its word or else the next word, so ``-l -o`` makes ``-o`` the value of
  ``-l``; a flag letter (``1 2 4 6 A a C f G g K k M N n q s T t V v X x Y y``) takes none and may be
  clustered (``-fN``); any other letter makes the argv unrecognised;
- among those options it carries ``-N``;
- it carries exactly one ``-o Tag=<ns>:<id>:<host>/<group>/<name>`` option (also spelled
  ``-oTag=...``), where ``<ns>`` is the tag namespace and ``<id>`` the attach id, and host, group,
  name and id each match camp's identifier rule ``[a-z0-9][a-z0-9-]*``;
- it carries at least one ``-L``, and every ``-L 127.0.0.1:<standard>:127.0.0.1:<published>``
  argument has ASCII-digit ports. A process whose ``-L`` has anything else is not recognised.

The decoys this rejects: another user's forward; a ``grep``/``tail`` that merely names the tag;
an ssh without ``-N`` (an interactive session); an ssh whose tag or ``-N`` is a word of a remote
command or follows the destination rather than being an option (``ssh box pgrep -N -o Tag=...``);
a tag that is the value of another option (``ssh -l -o Tag=... -N box``); a tagged ``-N`` ssh
with no ``-L``; two tag options; another namespace; a malformed host, group or name.

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
import sys
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from ..printable import printable_line
from .contract import is_identifier

DEFAULT_NAMESPACE = "camp-env"
NAMESPACE_ENV = "CAMP_ENV_TAG_NAMESPACE"

#: Seconds ``end`` waits after SIGTERM before it escalates to SIGKILL.
KILL_GRACE_SECONDS = 2.0

UNSEEN_HOLDER = "a process camp cannot see"

_ARG_OPTIONS = frozenset("BbcDEeFIiJLlmOoPpQRSWw")
_FLAG_OPTIONS = frozenset("1246AaCfGgKkMNnqsTtVvXxYy")
_FORWARD_RE = re.compile(r"127\.0\.0\.1:([1-9][0-9]{0,4}):127\.0\.0\.1:([1-9][0-9]{0,4})")
_MAX_PORT = 65535
_PS_ARGV = ["ps", "-axwwo", "pid=,uid=,command="]
_POLL_SECONDS = 0.05


class Unverifiable(Exception):
    """The process table could not be read; its message is the printable reason."""


class UnverifiableAfterSignal(Unverifiable):
    """The table could not be read after ``end`` had already sent a signal: the forward may have ended."""


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
    if not is_identifier(namespace):
        raise Untaggable(f"{NAMESPACE_ENV} is not a valid tag namespace: {printable_line(namespace)!r}")
    return namespace


def tag_for(host: str, group: str, name: str, attach_id: str) -> str:
    """The tag value ``<ns>:<id>:<host>/<group>/<name>`` for one attachment.

    The namespace is ``camp-env`` unless ``CAMP_ENV_TAG_NAMESPACE`` is set, read at
    call time. Raises :class:`Untaggable` for a namespace, host, group, name or id
    outside the rules above (a host with an uppercase letter or a dot, for one).
    """
    namespace = _namespace()
    for label, value in (("host", host), ("group", group), ("name", name), ("attach id", attach_id)):
        if not is_identifier(value):
            raise Untaggable(f"{label} {printable_line(value)!r} cannot be tagged")
    return f"{namespace}:{attach_id}:{host}/{group}/{name}"


def _default_ps(argv: Sequence[str], timeout: float) -> str:
    completed = subprocess.run(list(argv), capture_output=True, timeout=timeout)
    if completed.returncode != 0:
        detail = printable_line(completed.stderr.decode("utf-8", "replace").strip()) or "no message"
        raise Unverifiable(f"ps exited {completed.returncode}: {detail}")
    return completed.stdout.decode("utf-8", "replace")


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
    if not all(is_identifier(v) for v in (attach_id, host, group, name)):
        return None
    return attach_id, host, group, name


def _options(words: list[str]) -> "tuple[bool, list[tuple[str, str]]] | None":
    """Parse ssh's options as OpenSSH does: ``(has_N, [(letter, value) for -o and -L])``, or None
    when the argv is not one ssh would parse (an unknown letter, an option missing its value)."""
    has_n = False
    taken: list[tuple[str, str]] = []
    index = 1
    while index < len(words):
        word = words[index]
        index += 1
        if word == "--":
            break
        if not word.startswith("-") or word == "-":
            break
        position = 1
        while position < len(word):
            letter = word[position]
            if letter in _ARG_OPTIONS:
                value = word[position + 1:]
                if not value:
                    if index >= len(words):
                        return None
                    value = words[index]
                    index += 1
                if letter in "oL":
                    taken.append((letter, value))
                break
            if letter not in _FLAG_OPTIONS:
                return None
            has_n = has_n or letter == "N"
            position += 1
    return has_n, taken


def _recognise(pid: int, command: str, namespace: str) -> Attachment | None:
    words = command.split()
    if not words or os.path.basename(words[0]) != "ssh":
        return None
    parsed_options = _options(words)
    if parsed_options is None:
        return None
    has_n, taken = parsed_options
    if not has_n:
        return None
    tags = [value[len("Tag="):] for letter, value in taken if letter == "o" and value.startswith("Tag=")]
    specs = [value for letter, value in taken if letter == "L"]
    if len(tags) != 1 or not specs:
        return None
    parsed = _parse_tag(tags[0], namespace)
    if parsed is None:
        return None
    ports: list[tuple[int, int]] = []
    for spec in specs:
        match = _FORWARD_RE.fullmatch(spec)
        if match is None:
            return None
        standard, published = int(match.group(1)), int(match.group(2))
        if standard > _MAX_PORT or published > _MAX_PORT:
            return None
        ports.append((standard, published))
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
    attachments after the attempt (empty when everything ended). Raises plain
    :class:`Unverifiable` when the table cannot be read before the first signal (nothing was
    signalled), and :class:`UnverifiableAfterSignal` when a later read fails (SIGTERM was sent,
    so the forward may already have ended).
    """
    wanted = {a.pid: a.attach_id for a in found}
    if not wanted:
        return []
    terminated = _still_attachments(wanted, runner, uid)
    _signal(terminated, signal.SIGTERM)
    try:
        return _finish(wanted, terminated, runner, uid, grace)
    except UnverifiableAfterSignal:
        raise
    except Unverifiable as error:
        raise UnverifiableAfterSignal(str(error)) from None


def _finish(
    wanted: dict[int, str],
    terminated: set[int],
    runner: Callable[[list[str]], str] | None,
    uid: int | None,
    grace: float,
) -> list[int]:
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
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.bind((address, port))


def _default_lsof(argv: list[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True, timeout=5).stdout


_LOOPBACK_PROBED = (
    (socket.AF_INET, "127.0.0.1"),
    (socket.AF_INET6, "::1"),
)
_WILDCARD_PROBED = (
    (socket.AF_INET, "0.0.0.0"),
    (socket.AF_INET6, "::"),
)


def _probed(platform: str) -> "tuple[tuple[int, str], ...]":
    if platform.startswith("linux"):
        return _LOOPBACK_PROBED
    return _LOOPBACK_PROBED + _WILDCARD_PROBED


def _probe(
    bind: Callable[[int, str, int], None], port: int, platform: str
) -> "tuple[bool, str | None]":
    """``(held, unchecked)``: whether any probed address answers "in use", and, when a bind failed
    for another reason, that failure's printable text."""
    held = False
    unchecked: str | None = None
    for family, address in _probed(platform):
        try:
            bind(family, address, port)
        except OverflowError as error:
            unchecked = unchecked or printable_line(f"{error} on {address}").strip()
        except OSError as error:
            if error.errno == errno.EADDRINUSE:
                held = True
            elif family == socket.AF_INET6 and error.errno in (errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT):
                continue
            elif unchecked is None:
                unchecked = printable_line(f"{error.strerror or type(error).__name__} on {address}").strip()
    return held, unchecked


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
    platform: str | None = None,
) -> str | None:
    """None when *port* is free where ssh's ``-L 127.0.0.1:<port>`` would bind, else who holds it.

    camp binds the probed addresses itself, with SO_REUSEADDR set (IPV6_V6ONLY on the IPv6
    ones), as ssh does, and releases each at once. The probed addresses are 127.0.0.1 and ::1 on
    Linux, and those plus 0.0.0.0 and :: everywhere else (*platform*, default ``sys.platform``, is a
    test seam). On Linux a reuse bind of 127.0.0.1 already answers "in use" for a 0.0.0.0 holder
    and the ::1 probe for a ``::`` holder, while a wildcard probe would also answer "in use" for a
    listener on one specific non-loopback address (a tailnet IP, ``docker -p 192.168.1.5:8080:80``)
    that does not stop ssh binding 127.0.0.1; on macOS a 0.0.0.0 holder does not stop a 127.0.0.1
    reuse bind, so the wildcards are probed there. "Address in use" on any one probed address means held; a
    port whose only trace is TIME_WAIT is free, because ssh binds it too. An IPv6 bind failing
    because the machine has no IPv6 (``EADDRNOTAVAIL``, ``EAFNOSUPPORT``) counts as free on that
    family. Any other bind failure is a refusal, never an exception: the port is reported held by
    "a process that could not be checked" with the printable error text. A held port is described
    as ``<command> (pid <pid>)`` from ``lsof -F pcn`` output (the command passed through
    ``printable_line``), or as "a process camp cannot see" when lsof is absent or does not see it.
    *bind* (family, address, port) and *lsof_runner* (argv -> stdout) are test seams.

    The rule is measured, not assumed: ``tests/fixtures/env_port_probe_matrix.txt`` records, on
    Linux and macOS, every listener kind against all four addresses with and without
    SO_REUSEADDR, and a TIME_WAIT-only port. With SO_REUSEADDR on all four, every active listener
    answers "in use" on at least one address and a TIME_WAIT-only port is free on all four; on Linux
    the two loopback addresses alone do the same for every listener that can stop ssh's bind.
    """
    held, unchecked = _probe(bind or _default_bind, port, sys.platform if platform is None else platform)
    if held:
        return _describe(lsof_runner or _default_lsof, port)
    if unchecked is not None:
        return f"a process that could not be checked ({unchecked})"
    return None
