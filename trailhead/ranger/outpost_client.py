"""Outpost side of `ranger start`: health probe, operator credential, start call."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from pathlib import Path

from trailhead.launch_profile import validate_account
from trailhead.outpost_lifecycle import DAEMON_HOST, DAEMON_PORT, is_answering
from trailhead.paths import state_dir

HUB_SOCKET = "outpost-hubs"
START_TIMEOUT_SECONDS = 60.0


class OutpostRefusal(Exception):
    """A plain, human-readable reason the hub could not be started."""


def tmux_attach_argv(account: str) -> list[str]:
    validate_account(account)
    return ["tmux", "-L", HUB_SOCKET, "attach-session", "-t", f"=hub-{account}"]


def outpost_answering(port: int = DAEMON_PORT) -> bool:
    return is_answering(port)


def read_operator_credential(env: dict[str, str]) -> str:
    path: Path = state_dir("outpost", env=env) / "operator" / "credential"
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
        with urllib.request.urlopen(request, timeout=timeout):
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
