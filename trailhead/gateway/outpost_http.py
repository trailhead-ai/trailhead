"""Small loopback client for Outpost's HTTP routes, shared by the gateway commands.

Stdlib only. Talks to ``127.0.0.1`` directly: proxy environment variables (the
sandbox exports them) are ignored, redirects are refused, and every call has a
short timeout. Replies are JSON in and out; a non-2xx status is a result, not an
exception, so the caller can name the refusal. Only an unreachable Outpost raises.

A request body can be streamed from a file, and a reply can be read as it arrives
(``open_stream``). Neither has a whole-request timeout: ``IDLE_TIMEOUT_SECONDS`` bounds
each read and write on the socket, so a long run is followed for as long as it keeps
talking and a stalled connection is given up on.
"""

from __future__ import annotations

import base64
import json
import socket
import http.client
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO

from trailhead.outpost_lifecycle import DAEMON_HOST, DAEMON_PORT

CONNECT_TIMEOUT_SECONDS = 3.0
IDLE_TIMEOUT_SECONDS = 120.0


class OutpostUnreachable(Exception):
    """Outpost did not answer on the loopback port."""

    def __init__(self, port: int) -> None:
        super().__init__(f"{DAEMON_HOST}:{port}")
        self.port = port


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


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
    port: int = DAEMON_PORT,
    timeout: float = CONNECT_TIMEOUT_SECONDS,
) -> Reply:
    """One loopback call. *form* is sent urlencoded, *json_body* as JSON; the reply body is parsed as a JSON object.

    *body_file* is ``(file, length, content_type)``: the file is read and sent as it is written to the socket, never
    held whole in memory, and the idle timeout replaces *timeout* so a large upload is not cut off by a short one.
    """
    data: object = urllib.parse.urlencode(form).encode() if form is not None else None
    if json_body is not None:
        data = json.dumps(json_body).encode()
    if body_file is not None:
        data = body_file[0]
        timeout = IDLE_TIMEOUT_SECONDS
    req = urllib.request.Request(f"http://{DAEMON_HOST}:{port}{path}", data=data, method=method)
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    if body_file is not None:
        req.add_header("Content-Length", str(body_file[1]))
        req.add_header("Content-Type", body_file[2])
    elif json_body is not None:
        req.add_header("Content-Type", "application/json")
    elif data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with _opener().open(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read()
    except (urllib.error.URLError, OSError) as err:
        raise OutpostUnreachable(port) from err
    return _reply(status, raw)


def _reply(status: int, raw: bytes) -> Reply:
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    return Reply(status, body if isinstance(body, dict) else {})


class Stream:
    """An open reply read as it arrives. A non-2xx status is in ``refusal``; otherwise ``lines()`` yields the body."""

    def __init__(self, resp, refusal: Reply | None) -> None:
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


def open_stream(
    path: str,
    *,
    headers: dict[str, str] | None = None,
    port: int = DAEMON_PORT,
) -> Stream:
    """GET *path* and hand back the reply unread, each read bounded by the idle timeout."""
    req = urllib.request.Request(f"http://{DAEMON_HOST}:{port}{path}", method="GET")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        resp = _opener().open(req, timeout=IDLE_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as err:
        try:
            raw = err.read()
        except (http.client.HTTPException, OSError) as broken:
            raise OutpostUnreachable(port) from broken
        return Stream(err, _reply(err.code, raw))
    except (urllib.error.URLError, OSError) as err:
        raise OutpostUnreachable(port) from err
    return Stream(resp, None)
