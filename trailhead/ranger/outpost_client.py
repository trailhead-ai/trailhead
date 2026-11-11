"""Outpost side of `ranger start`: health probe, operator credential, start call."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

from trailhead.launch_profile import operator_token_store, validate_account
from trailhead.outpost_lifecycle import DAEMON_HOST, DAEMON_PORT

HUB_SOCKET = "outpost-hubs"
# Outpost's worst case for one start request, from its server bounds (server/operator/hubs.ts,
# server/timeline/reconciler.ts): the pre-start reconcile's has-session, list-panes and kill
# 30s, then has-session, list-panes and kill 30s, trailhead session-start 30s, new-session 10s,
# settle 1.5s, list-panes, capture-pane and rollback kill 30s = 131.5s. The per-account lock
# can make the request wait behind one more such request: 263s. 300s covers both with margin.
START_TIMEOUT_SECONDS = 300.0


# Hub start refuses a launch with no broker session; this id passes the shape check so the
# diagnostic reaches the real refusal. session-start only prints a launch, so it has no effect.
DIAGNOSTIC_BROKER_SESSION = "diagnostic"


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


def _diagnostic(account: str) -> str:
    return (
        f"run `trailhead session-start --role hub --account {account} --json "
        f"--broker-session {DIAGNOSTIC_BROKER_SESSION}` to see why"
    )


#: Outpost answers these when trailhead's own launch was refused or its output was unusable.
_DIAGNOSABLE = ("trailhead_refused", "trailhead_bad_output")


def _canned_detail(error: object, account: str) -> str | None:
    """The fix to print for an Outpost error code whose reply carried no message."""
    return {
        "trailhead_refused": f"the hub launch was refused; {_diagnostic(account)}",
        "trailhead_bad_output": f"the hub launch was refused; {_diagnostic(account)}",
        "tmux_failed": "tmux failed; run `trailhead doctor`",
        "invalid_account": f"the account name {account!r} is invalid",
        "internal": "Outpost failed; check its log with `trailhead outpost status`",
    }.get(error) if isinstance(error, str) else None


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
        message = body.get("message")
        if message and body.get("error") in _DIAGNOSABLE:
            message = f"{message}; {_diagnostic(account)}"
        detail = (
            message
            or _canned_detail(body.get("error"), account)
            or body.get("error")
            or f"HTTP {exc.code}"
        )
        step = body.get("step")
        raise OutpostRefusal(f"Outpost could not start the hub: {detail}" + (f" (step: {step})" if step else ""))
    except (socket.timeout, TimeoutError):
        raise OutpostRefusal(f"timed out after {timeout:g}s waiting for Outpost to start the hub")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise OutpostRefusal(f"timed out after {timeout:g}s waiting for Outpost to start the hub")
        raise OutpostRefusal(f"could not reach Outpost: {exc.reason}")
