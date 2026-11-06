"""An attachment: one background ``ssh`` forward or local relay, found again from the process table.

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

A **relay** (``python -m camp.env.relay``, the stand-in for a forward when the environment is on
this machine) is recognised by a second shape. The same user, ``-N``, one tag and ``-L`` rules hold,
read by the same option parser, with these differences:

- its argv0 is either exactly ``camp-env-relay`` (what ``start_relay`` asks for; Linux keeps it) or
  an absolute interpreter path whose basename starts with ``python`` or is exactly ``Python``
  (macOS re-execs Homebrew's python into ``.../Python.app/Contents/MacOS/Python``, and ``ps`` shows
  that path), so a relative ``python3``, ``bash``, ``/usr/bin/env`` and ``grep`` are decoys;
- words 1 and 2 are exactly ``-m camp.env.relay``, and options are read from word 3;
- its tag host is ``local`` (a relay carries only this machine's environments), so a relay-shaped
  row tagged with any other host is not recognised;
- every ``-L`` port also satisfies the relay's own rule, ``camp.env.relay.port_in_range``
  (1024-65535), so a relay row with a port below 1024 is not recognised, while a forward keeps 1-65535.

A row is read by splitting ``ps``'s command on whitespace, so on macOS (where ``ps`` shows the
interpreter's path) a relay started from an interpreter at a path containing whitespace is never
recognised: attach then fails safely and ends the relay through its handle.

Nothing in a relay's argv names where camp lives: ``start_relay`` passes that in its environment, so
a relay started by one camp checkout is recognised from any other, whatever its path holds.
:attr:`Attachment.kind` says which shape a row had.

``CAMP_ENV_TAG_NAMESPACE`` replaces the namespace ``camp-env`` (the test seam). It
is read on every call, and a value outside camp's identifier rule is refused.
"""
from __future__ import annotations

import errno
import os
import re
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from ..printable import printable_line
from .contract import is_identifier
from .relay import port_in_range

DEFAULT_NAMESPACE = "camp-env"
NAMESPACE_ENV = "CAMP_ENV_TAG_NAMESPACE"

#: Seconds ``end`` waits after SIGTERM before it escalates to SIGKILL.
KILL_GRACE_SECONDS = 2.0

UNSEEN_HOLDER = "a process camp cannot see"

_ARG_OPTIONS = frozenset("BbcDEeFIiJLlmOoPpQRSWw")
_FLAG_OPTIONS = frozenset("1246AaCfGgKkMNnqsTtVvXxYy")
_FORWARD_RE = re.compile(r"127\.0\.0\.1:([1-9][0-9]{0,4}):127\.0\.0\.1:([1-9][0-9]{0,4})")
_MAX_PORT = 65535
_RELAY_ARGV0 = "camp-env-relay"
_RELAY_MODULE_WORDS = ["-m", "camp.env.relay"]
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
    """One recognised forward or relay. ``ports`` holds ``(standard, published)`` pairs
    read from its ``-L`` arguments, ordered by standard port; ``kind`` is ``"forward"`` or ``"relay"``."""

    pid: int
    host: str
    group: str
    name: str
    ports: tuple[tuple[int, int], ...]
    attach_id: str
    kind: str = "forward"


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


def _options(words: list[str], start: int = 1) -> "tuple[bool, list[tuple[str, str]]] | None":
    """Parse ssh's options as OpenSSH does, from word *start*: ``(has_N, [(letter, value) for -o and -L])``,
    or None when the argv is not one ssh would parse (an unknown letter, an option missing its value)."""
    has_n = False
    taken: list[tuple[str, str]] = []
    index = start
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


def _is_interpreter(argv0: str) -> bool:
    base = os.path.basename(argv0)
    return os.path.isabs(argv0) and (base.startswith("python") or base == "Python")


def _shape(words: list[str]) -> "tuple[str, int] | None":
    """``(kind, index of the first option)`` for a row's words, or None when it has neither shape."""
    if not words:
        return None
    if os.path.basename(words[0]) == "ssh":
        return "forward", 1
    if words[1:3] == _RELAY_MODULE_WORDS and (words[0] == _RELAY_ARGV0 or _is_interpreter(words[0])):
        return "relay", 3
    return None


def _recognise(pid: int, command: str, namespace: str) -> Attachment | None:
    words = command.split()
    shape = _shape(words)
    if shape is None:
        return None
    kind, start = shape
    parsed_options = _options(words, start)
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
        if kind == "relay" and not (port_in_range(standard) and port_in_range(published)):
            return None
        ports.append((standard, published))
    attach_id, host, group, name = parsed
    if kind == "relay" and host != "local":
        return None
    return Attachment(pid, host, group, name, tuple(sorted(ports)), attach_id, kind)


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


def relay_argv(ports: Sequence[tuple[int, int]], tag: str) -> list[str]:
    """The argv a relay is started with: ``camp-env-relay -m camp.env.relay -N -o Tag=<tag> -L ...``
    for each ``(standard, published)`` pair."""
    argv = [_RELAY_ARGV0, *_RELAY_MODULE_WORDS, "-N", "-o", f"Tag={tag}"]
    for standard, published in ports:
        argv += ["-L", f"127.0.0.1:{standard}:127.0.0.1:{published}"]
    return argv


def _package_root() -> str:
    """The directory holding the ``camp`` package this module belongs to."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _relay_environment() -> dict[str, str]:
    return {
        "PYTHONPATH": _package_root(),
        "PYTHONSAFEPATH": "1",
        "PYTHONHOME": f"{sys.base_prefix}:{sys.base_exec_prefix}",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/"),
    }


def end_spawned(process: subprocess.Popen) -> None:
    """End and reap a relay this process spawned itself, through its handle.

    SIGTERM, then SIGKILL after the kill grace. It signals only that child, never a
    pid read from the process table, so it is safe on every exit path of an attach.
    """
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _await_ready(process: subprocess.Popen, timeout: float) -> "str | None":
    """None once the relay has printed ``ready``; the timeout reason when it has not by *timeout*
    seconds; ``""`` when its stdout closed or said something else."""
    fd = process.stdout.fileno()
    deadline = time.monotonic() + timeout
    seen = b""
    while b"\n" not in seen:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return f"the relay was not ready within {timeout:g}s"
        if not select.select([fd], [], [], remaining)[0]:
            continue
        chunk = os.read(fd, 1024)
        if not chunk:
            return ""
        seen += chunk
    return None if seen.split(b"\n", 1)[0].strip() == b"ready" else ""


_RELAY_PORT_LINE = re.compile(r"port [0-9]+: ")


def _why_it_stopped(process: subprocess.Popen, errors) -> str:
    try:
        process.wait(KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    end_spawned(process)
    errors.seek(0)
    lines = errors.read().decode("utf-8", "replace").strip().splitlines()
    own = next((line for line in lines if _RELAY_PORT_LINE.match(line)), None)
    if own is None:
        own = next((line for line in lines if line.strip()), "")
    first = printable_line(own).strip()
    return first or f"the relay exited with status {process.returncode} before it was ready"


def start_relay(
    ports: Sequence[tuple[int, int]], tag: str, *, timeout: float
) -> "subprocess.Popen | str":
    """Start a relay holding each ``(standard, published)`` pair and wait up to *timeout* seconds for ``ready``.

    Returns the started process handle, or a one-line printable reason (the relay's own
    ``port <n>: ...`` line, its exit status, or a timeout) after ending what it started. The relay
    gets an environment built from scratch (``PYTHONPATH`` camp's own package root,
    ``PYTHONSAFEPATH=1``, ``PYTHONHOME`` this interpreter's base prefixes, ``PATH``,
    ``HOME``), runs from ``/`` in a session of its own, and
    is started with ``sys.executable`` under the argv0 ``camp-env-relay``. Its stderr goes to a
    temporary file removed before this returns.
    """
    with tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen(
                relay_argv(ports, tag), executable=sys.executable, env=_relay_environment(), cwd="/",
                start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors,
            )
        except OSError as error:
            return f"the relay could not be started: {printable_line(str(error))}"
        try:
            problem = _await_ready(process, timeout)
            if problem is None:
                return process
            if problem:
                end_spawned(process)
                return printable_line(problem).strip()
            return _why_it_stopped(process, errors)
        except BaseException:
            end_spawned(process)
            raise
        finally:
            process.stdout.close()


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
