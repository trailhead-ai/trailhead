"""Small loopback client for Outpost's HTTP routes, shared by the gateway commands.

Stdlib only. Talks to ``127.0.0.1`` directly: proxy environment variables (the
sandbox exports them) are ignored, redirects are refused, and every call has a
short timeout. Replies are JSON in and out; a non-2xx status is a result, not an
exception, so the caller can name the refusal. Only an unreachable Outpost raises.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from trailhead.outpost_lifecycle import DAEMON_HOST, DAEMON_PORT

CONNECT_TIMEOUT_SECONDS = 3.0


class OutpostUnreachable(Exception):
    """Outpost did not answer on the loopback port."""

    def __init__(self, port: int) -> None:
        super().__init__(f"{DAEMON_HOST}:{port}")
        self.port = port


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
    port: int = DAEMON_PORT,
    timeout: float = CONNECT_TIMEOUT_SECONDS,
) -> Reply:
    """One loopback call. *form* is sent urlencoded, *json_body* as JSON; the reply body is parsed as a JSON object."""
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    if json_body is not None:
        data = json.dumps(json_body).encode()
    req = urllib.request.Request(f"http://{DAEMON_HOST}:{port}{path}", data=data, method=method)
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    if json_body is not None:
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
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    return Reply(status, body if isinstance(body, dict) else {})
