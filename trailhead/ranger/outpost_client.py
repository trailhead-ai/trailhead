"""Outpost side of `ranger start`: health probe, operator credential, start call."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

from trailhead.launch_profile import operator_token_store, validate_account
from trailhead.outpost_lifecycle import DAEMON_HOST, DAEMON_PORT

HUB_SOCKET = "outpost-hubs"
# Outpost's worst case for one start request, from its server bounds (server/operator/hubs.ts):
# has-session 10s + trailhead session-start 30s + new-session 10s + settle 1.5s +
# list/capture/kill up to 30s = 81.5s, and the per-account lock can make the request wait
# behind one more such request. 180s covers both with margin.
START_TIMEOUT_SECONDS = 180.0


class OutpostRefusal(Exception):
    """A plain, human-readable reason the hub could not be started."""


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OutpostRefusal(f"Outpost answered with a redirect (HTTP {code}); refusing to follow it")


def _opener() -> urllib.request.OpenerDirector:
    """Loopback-only opener: ignores proxy environment variables and never follows redirects."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _RefuseRedirect)


def tmux_attach_argv(account: str) -> list[str]:
    validate_account(account)
    return ["tmux", "-L", HUB_SOCKET, "attach-session", "-t", f"=hub-{account}"]


def outpost_answering(port: int = DAEMON_PORT) -> bool:
    try:
        with _opener().open(f"http://{DAEMON_HOST}:{port}/health", timeout=1.0) as resp:
            health = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError, OutpostRefusal):
        return False
    return (
        isinstance(health, dict)
        and health.get("ok") is True
        and isinstance(health.get("contract_version"), int)
        and not isinstance(health.get("contract_version"), bool)
    )


def read_operator_credential(env: dict[str, str]) -> str:
    path = operator_token_store(env=env) / "credential"
    try:
        value = path.read_text().strip()
    except OSError:
        value = ""
    if not value:
        raise OutpostRefusal(
            "no operator credential is available from Outpost; restart it with "
            "`trailhead outpost restart`"
        )
    return value


def _body(exc: urllib.error.HTTPError) -> dict:
    try:
        data = json.loads(exc.read())
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def start_hub(
    account: str,
    credential: str,
    *,
    port: int = DAEMON_PORT,
    timeout: float = START_TIMEOUT_SECONDS,
) -> None:
    """Ask Outpost to start the hub for ``account``; returns on 200, raises OutpostRefusal otherwise."""
    validate_account(account)
    request = urllib.request.Request(
        f"http://{DAEMON_HOST}:{port}/operator/hubs/{account}/start",
        method="POST",
        data=b"",
        headers={"Authorization": f"Bearer {credential}"},
    )
    try:
        with _opener().open(request, timeout=timeout):
            return
    except urllib.error.HTTPError as exc:
        body = _body(exc)
        if exc.code == 401:
            raise OutpostRefusal(
                "Outpost rejected the operator credential; restart it with "
                "`trailhead outpost restart`"
            )
        if body.get("error") == "hub_exited_at_launch":
            raise OutpostRefusal(f"hub exited at launch:\n{body.get('output', '')}")
        if exc.code == 504:
            raise OutpostRefusal("Outpost timed out starting the hub")
        detail = body.get("message") or body.get("error") or f"HTTP {exc.code}"
        step = body.get("step")
        raise OutpostRefusal(f"Outpost could not start the hub: {detail}" + (f" (step: {step})" if step else ""))
    except (socket.timeout, TimeoutError):
        raise OutpostRefusal(f"timed out after {timeout:g}s waiting for Outpost to start the hub")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise OutpostRefusal(f"timed out after {timeout:g}s waiting for Outpost to start the hub")
        raise OutpostRefusal(f"could not reach Outpost: {exc.reason}")
