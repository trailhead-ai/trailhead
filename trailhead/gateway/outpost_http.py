"""Small client for Outpost's gateway routes, shared by the gateway commands.

Stdlib only. Talks HTTP over the unix socket Outpost serves the gateway on, at a fixed place under
Outpost's state folder (``socket_path``). The socket path is derived from that folder the way Outpost derives it
(``OUTPOST_STATE_DIR``, then ``XDG_STATE_HOME``); there is no argument or variable that names the socket, and no
TCP: a credential, token or registration authority is never sent to a port, so a process squatting on one is sent
nothing. The folder is operator-only, which is the access control: a session that cannot write it cannot plant a
socket there. Proxy environment variables do not apply to a socket, redirects are refused, and every call has a short
timeout. Replies are JSON in and out; a non-2xx status is a result, not an exception, so the caller can name the
refusal. Only an unreachable Outpost raises: ``OutpostUnreachable`` when the socket is missing or refuses (Outpost is
not running), ``SocketNotReachable`` when the operating system denies this process the socket.

Reaching Outpost has one overall budget (``reach_budget``): every call made without an
explicit timeout draws on what is left of it until the caller says ``reached``, so a command
that registers, exchanges a token and makes its first call is bounded as a whole. After that each
further call has ``CONNECT_TIMEOUT_SECONDS`` to itself.

A request body can be streamed from a file, and a reply can be read as it arrives
(``open_stream``). Neither has a whole-request timeout: ``IDLE_TIMEOUT_SECONDS`` bounds
each read and write on the socket, so a long run is followed for as long as it keeps
talking and a stalled connection is given up on.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import socket
import stat
import time
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from trailhead.outpost_lifecycle import APP as OUTPOST_APP
from trailhead.paths import state_dir

CONNECT_TIMEOUT_SECONDS = 5.0
REACH_BUDGET_SECONDS = 4.5
"""Time allowed to reach Outpost: a little under five seconds, so a command that cannot reach it
has also started its interpreter and printed its message within five."""
IDLE_TIMEOUT_SECONDS = 120.0
MAX_REPLY_BYTES = 64 * 1024
"""The most of a reply body read for a request or a refusal: Outpost's JSON answers are small, and a larger one is
refused rather than held in memory."""

_deadline: float | None = None


def socket_path(env: dict[str, str] | None = None) -> Path:
    """Outpost's gateway socket: ``gateway/gateway.sock`` under Outpost's state folder. *env* is the environment the
    folder is derived from (the process's by default); it selects the folder, never the socket."""
    return state_dir(OUTPOST_APP, env=env) / "gateway" / "gateway.sock"


class OutpostUnreachable(Exception):
    """Outpost did not answer on its gateway socket: there is none, or nothing accepts on it."""

    def __init__(self, path: Path) -> None:
        super().__init__(str(path))
        self.path = path
        self.message = f"Outpost is unreachable at {path}; is it running?"


class SocketNotReachable(Exception):
    """The operating system denies this process Outpost's gateway socket."""

    def __init__(self, path: Path) -> None:
        super().__init__(str(path))
        self.path = path
        self.message = f"Outpost's socket at {path} is not reachable from here (permission denied)"


class UnsafeSocket(SocketNotReachable):
    """The gateway socket or its folder is not what Outpost makes for the operator alone, so nothing was sent.
    ``reason`` is one of folder-not-a-directory, folder-owner, folder-mode, not-a-socket, socket-owner."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(path)
        self.reason = reason
        self.message = f"Outpost's socket at {path} is not safe to use ({reason}); refusing to send anything to it"


class UnexpectedReply(OutpostUnreachable):
    """Outpost's reply body is larger than ``MAX_REPLY_BYTES``, which no answer it gives is."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.message = f"Outpost's reply at {path} was larger than {MAX_REPLY_BYTES} bytes; refusing it"


class ReplyTimedOut(OutpostUnreachable):
    """The request was sent and Outpost did not answer in time: it may or may not have acted on it."""


class StreamBroken(Exception):
    """A streamed exchange stopped mid-way: the peer went quiet past the idle timeout or dropped the connection."""

    def __init__(self, timed_out: bool) -> None:
        super().__init__("timed out" if timed_out else "connection dropped")
        self.timed_out = timed_out


@dataclass(frozen=True)
class Reply:
    status: int
    body: dict
    """The decoded JSON body when it is an object, else an empty dict."""


class _SocketConnection(http.client.HTTPConnection):
    def __init__(self, path: Path, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except PermissionError:
            raise SocketNotReachable(self._path) from None
        try:
            sock.settimeout(self.timeout)
            sock.connect(str(self._path))
        except PermissionError:
            sock.close()
            raise SocketNotReachable(self._path) from None
        except OSError:
            sock.close()
            raise OutpostUnreachable(self._path) from None
        self.sock = sock


_lstat = os.lstat


def _operator_uid() -> int:
    return os.geteuid()


def _check_socket_site(path: Path) -> None:
    """Refuse, before anything is sent, a socket folder that is a link, is not ours or is open to group or other,
    and a gateway path that is not a socket we own. A missing path or one in a folder we cannot enter is reported as
    it always was."""
    uid = _operator_uid()
    try:
        folder = _lstat(path.parent)
        info = _lstat(path)
    except PermissionError:
        raise SocketNotReachable(path) from None
    except OSError:
        raise OutpostUnreachable(path) from None
    if not stat.S_ISDIR(folder.st_mode):
        raise UnsafeSocket(path, "folder-not-a-directory")
    if folder.st_uid != uid:
        raise UnsafeSocket(path, "folder-owner")
    if stat.S_IMODE(folder.st_mode) & 0o077:
        raise UnsafeSocket(path, "folder-mode")
    if not stat.S_ISSOCK(info.st_mode):
        raise UnsafeSocket(path, "not-a-socket")
    if info.st_uid != uid:
        raise UnsafeSocket(path, "socket-owner")


def _connection(timeout: float, env: dict[str, str] | None = None) -> _SocketConnection:
    """A connection to Outpost's socket, after :func:`_check_socket_site` has passed it."""
    path = socket_path(env)
    _check_socket_site(path)
    return _SocketConnection(path, timeout)


def start_reach_budget() -> None:
    """Give the calls made from now until :func:`reached` ``REACH_BUDGET_SECONDS`` between them."""
    global _deadline
    _deadline = time.monotonic() + REACH_BUDGET_SECONDS


@contextmanager
def reach_budget() -> Iterator[None]:
    """:func:`start_reach_budget` for the calls made inside, and no budget after."""
    start_reach_budget()
    try:
        yield
    finally:
        reached()


def reached() -> None:
    """End the reach budget: the caller has what it needed from Outpost, and later calls have their own timeouts."""
    global _deadline
    _deadline = None


def _timeout_for(timeout: float | None, env: dict[str, str] | None = None) -> float:
    if timeout is not None:
        return timeout
    if _deadline is None:
        return CONNECT_TIMEOUT_SECONDS
    remaining = _deadline - time.monotonic()
    if remaining <= 0:
        raise OutpostUnreachable(socket_path(env))
    return remaining


def basic_auth(client_id: str, client_secret: str) -> str:
    return "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


def request(
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
    json_body: object | None = None,
    body_file: tuple[BinaryIO, int, str] | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> Reply:
    """One call over the gateway socket. *form* is sent urlencoded, *json_body* as JSON; the reply body is parsed as a
    JSON object.

    *body_file* is ``(file, length, content_type)``: the file is read and sent as it is written to the socket, never
    held whole in memory, and the idle timeout replaces *timeout* so a large upload is not cut off by a short one.
    Without a *timeout* the call draws on the reach budget while one is running, else ``CONNECT_TIMEOUT_SECONDS``.
    A call whose request was sent and got no answer in time raises :class:`ReplyTimedOut`.
    """
    send_headers = dict(headers or {})
    data: object = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        send_headers["Content-Type"] = "application/x-www-form-urlencoded"
    if json_body is not None:
        data = json.dumps(json_body).encode()
        send_headers["Content-Type"] = "application/json"
    if body_file is not None:
        data = body_file[0]
        timeout = IDLE_TIMEOUT_SECONDS
        send_headers["Content-Length"] = str(body_file[1])
        send_headers["Content-Type"] = body_file[2]
    send_headers["Connection"] = "close"
    conn = _connection(_timeout_for(timeout, env), env)
    try:
        conn.request(method, path, body=data, headers=send_headers)
        resp = conn.getresponse()
        status, raw = resp.status, _read_body(resp, conn._path)
    except (SocketNotReachable, OutpostUnreachable):
        raise
    except TimeoutError as err:
        raise ReplyTimedOut(conn._path) from err
    except (http.client.HTTPException, OSError) as err:
        raise OutpostUnreachable(conn._path) from err
    finally:
        conn.close()
    return _reply(status, raw)


def _read_body(resp: http.client.HTTPResponse, path: Path) -> bytes:
    raw = resp.read(MAX_REPLY_BYTES + 1)
    if len(raw) > MAX_REPLY_BYTES:
        raise UnexpectedReply(path)
    return raw


def _reply(status: int, raw: bytes) -> Reply:
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    return Reply(status, body if isinstance(body, dict) else {})


class Stream:
    """An open reply read as it arrives. A non-2xx status is in ``refusal``; otherwise ``lines()`` yields the body."""

    def __init__(self, conn: http.client.HTTPConnection, resp: http.client.HTTPResponse, refusal: Reply | None) -> None:
        self._conn = conn
        self._resp = resp
        self.refusal = refusal

    def lines(self) -> Iterator[bytes]:
        try:
            while line := self._resp.readline():
                yield line
        except (TimeoutError, socket.timeout) as err:
            raise StreamBroken(True) from err
        except (http.client.HTTPException, OSError) as err:
            raise StreamBroken(False) from err

    def close(self) -> None:
        self._resp.close()
        self._conn.close()


def open_stream(path: str, *, headers: dict[str, str] | None = None) -> Stream:
    """GET *path* and hand back the reply unread, each read bounded by the idle timeout."""
    conn = _connection(IDLE_TIMEOUT_SECONDS)
    try:
        conn.request("GET", path, headers={**(headers or {}), "Connection": "close"})
        resp = conn.getresponse()
        if not 200 <= resp.status < 300:
            raw = _read_body(resp, conn._path)
            refusal = _reply(resp.status, raw)
            return Stream(conn, resp, refusal)
    except (SocketNotReachable, OutpostUnreachable):
        conn.close()
        raise
    except (http.client.HTTPException, OSError) as err:
        conn.close()
        raise OutpostUnreachable(conn._path) from err
    return Stream(conn, resp, None)
