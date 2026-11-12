"""``trailhead gateway register`` — register this session with Outpost's gateway.

Run from the Outpost plugin's SessionStart hook (``--hook``). It finds the harness
process that owns it through the harness seam, reads the registration authority
from Outpost's state directory, and posts ``{pid, start_time}`` to Outpost, which
derives the workspace itself. Nothing is taken from the caller: no argument,
environment variable or hook input names a process, workspace or session key.

As a hook it never blocks the session: it always exits 0 and prints nothing on
success. A failure writes one reason code to stderr and appends one line to
``<trailhead state>/gateway/register.log``. The authority is never printed or logged.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from trailhead.gateway import outpost_http
from trailhead.harness import harness_process
from trailhead.outpost_lifecycle import APP as OUTPOST_APP
from trailhead.outpost_lifecycle import DAEMON_PORT
from trailhead.paths import state_dir

PORT = DAEMON_PORT
TIMEOUT_SECONDS = 1.5

# Reason codes Outpost's registration route answers with; anything else is
# reported as UNEXPECTED_REPLY so a server-supplied string is never echoed.
OUTPOST_REASONS = frozenset(
    {
        "registration_authority_required",
        "invalid_request",
        "process_mismatch",
        "not_in_workspace",
        "host_not_allowed",
        "internal",
    }
)
NO_HARNESS = "no_harness"
AUTHORITY_MISSING = "authority_missing"
AUTHORITY_UNREADABLE = "authority_unreadable"
OUTPOST_UNREACHABLE = "outpost_unreachable"
UNEXPECTED_REPLY = "unexpected_reply"
UNEXPECTED_FAILURE = "unexpected_failure"


class Refused(Exception):
    def __init__(self, reason: str, key: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.key = key


def _read_authority() -> str:
    path = state_dir(OUTPOST_APP) / "registration" / "credential"
    try:
        authority = path.read_text().strip()
    except FileNotFoundError:
        raise Refused(AUTHORITY_MISSING) from None
    except (OSError, UnicodeDecodeError):
        raise Refused(AUTHORITY_UNREADABLE) from None
    if not authority:
        raise Refused(AUTHORITY_MISSING)
    if not authority.isprintable():
        raise Refused(AUTHORITY_UNREADABLE)
    return authority


def register() -> str:
    """Register this process's harness; return the session key or raise :class:`Refused`."""
    found = harness_process()
    if found is None:
        raise Refused(NO_HARNESS)
    pid, start_time = found
    local_key = f"ws-{pid}-{start_time}"
    authority = _read_authority()
    try:
        reply = outpost_http.request(
            "POST",
            "/gateway/sessions",
            headers={"Authorization": f"Bearer {authority}"},
            json_body={"pid": pid, "start_time": start_time},
            port=PORT,
            timeout=TIMEOUT_SECONDS,
        )
    except outpost_http.OutpostUnreachable:
        raise Refused(OUTPOST_UNREACHABLE, local_key) from None
    body = reply.body if isinstance(reply.body, dict) else {}
    if reply.status == 200 and isinstance(body.get("session_key"), str):
        return local_key
    reason = body.get("reason")
    if reply.status != 200 and reason in OUTPOST_REASONS:
        raise Refused(reason, local_key)
    raise Refused(UNEXPECTED_REPLY, local_key)


def _log(reason: str, key: str | None) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        path = state_dir("trailhead") / "gateway" / "register.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(f"{stamp} {key or '-'} {reason}\n")
    except Exception:  # noqa: BLE001 — a log that cannot be written must not block the session
        pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trailhead gateway register",
        description="Register this session with Outpost's gateway.",
    )
    parser.add_argument(
        "--hook",
        action="store_true",
        help="run as a SessionStart hook: silent on success, always exit 0",
    )
    return parser


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        key = register()
    except Refused as refusal:
        reason, key = refusal.reason, refusal.key
    except Exception:  # noqa: BLE001 — a traceback must never carry the authority
        reason, key = UNEXPECTED_FAILURE, None
    else:
        if not args.hook:
            print(f"registered {key}")
        return 0
    _log(reason, key)
    print(reason, file=sys.stderr)
    return 0 if args.hook else 1
