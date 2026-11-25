"""``trailhead gateway register`` — register this session with Outpost's gateway.

Run from the Outpost plugin's SessionStart hook (``--hook``) through the pinned
``trailhead-gateway-register`` shim install writes, and by hand as ``trailhead gateway
register``. Everything it imports is stdlib or trailhead, so it runs on the isolated
interpreter that shim starts. It finds the harness
process that owns it through the harness seam, reads the registration authority
from Outpost's state directory, and posts ``{pid, start_time}`` to Outpost, which
derives the workspace itself. Nothing is taken from the caller: no argument,
environment variable or hook input names a process, workspace or session key.

As a hook it never blocks the session: it always exits 0, within ``HOOK_TIMEOUT_SECONDS``
of reaching Outpost (the hook's declared timeout discards the output of a hook that
overruns it), and prints nothing on success. A failure writes one reason code to stderr and
appends ``<timestamp> <key> <reason> <line>`` to ``<trailhead state>/gateway/register.log``,
where ``<line>`` is the one-line statement that the session has no gateway credential and why;
the hook also prints that line to the session as one JSON object on stdout. The authority is
never printed or logged.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from trailhead.gateway import outpost_http
from trailhead.harness import format_session_key, harness_process
from trailhead.outpost_lifecycle import APP as OUTPOST_APP
from trailhead.outpost_lifecycle import DAEMON_PORT
from trailhead.paths import state_dir

PORT = DAEMON_PORT
TIMEOUT_SECONDS = 1.5
HOOK_TIMEOUT_SECONDS = 1.0
"""What the SessionStart hook allows Outpost to answer in, under the hook's declared 2 s timeout with
the interpreter's start inside it."""

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


def register_log() -> Path:
    """Where a failed registration leaves its one-line reason."""
    return state_dir("trailhead") / "gateway" / "register.log"


NEXT_STEPS = {
    NO_HARNESS: "Run this inside a session started by a supported harness.",
    AUTHORITY_MISSING: "Start Outpost so it writes its registration authority, then try again.",
    AUTHORITY_UNREADABLE: "This process cannot read Outpost's registration authority; run it where that file is readable.",
    OUTPOST_UNREACHABLE: "Start Outpost, then try again.",
    UNEXPECTED_REPLY: "Outpost answered the registration with a reply this command cannot read.",
    UNEXPECTED_FAILURE: "Registration failed unexpectedly; try again.",
}
"""What to do next for a registration that failed before Outpost gave a reason of its own."""


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _one_line(text: str) -> str:
    """*text* as one line without control characters, so it cannot drive the terminal that shows it."""
    return " ".join(_CONTROL.sub("", text).split())


class Refused(Exception):
    """A registration that did not succeed: *reason* is a code from this module, *message* Outpost's next step if it gave one."""

    def __init__(self, reason: str, key: str | None = None, message: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.key = key
        self.message = message


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


def register(*, port: int | None = None, timeout: float | None = TIMEOUT_SECONDS) -> str:
    """Register this process's harness; return the session key or raise :class:`Refused`.

    *port* defaults to ``PORT``; a *timeout* of ``None`` leaves the call to the reach budget of the command that runs it."""
    found = harness_process()
    if found is None:
        raise Refused(NO_HARNESS)
    pid, start_time = found
    local_key = format_session_key(pid, start_time)
    authority = _read_authority()
    try:
        reply = outpost_http.request(
            "POST",
            "/gateway/sessions",
            headers={"Authorization": f"Bearer {authority}"},
            json_body={"pid": pid, "start_time": start_time},
            port=PORT if port is None else port,
            timeout=timeout,
        )
    except outpost_http.OutpostUnreachable:
        raise Refused(OUTPOST_UNREACHABLE, local_key) from None
    if reply.status == 200 and isinstance(reply.body.get("session_key"), str):
        return local_key
    reason = reply.body.get("reason")
    if reply.status != 200 and reason in OUTPOST_REASONS:
        message = reply.body.get("message")
        raise Refused(reason, local_key, message if isinstance(message, str) and message else None)
    raise Refused(UNEXPECTED_REPLY, local_key)


NO_CREDENTIAL = "This session has no gateway credential"


def no_credential_line(reason: str, message: str | None = None) -> str:
    """The one line saying this session has no gateway credential and why, for a failure with *reason* and Outpost's *message*."""
    if reason == OUTPOST_UNREACHABLE:
        why = "Outpost is unreachable; it will register on the first gateway call once Outpost is back"
    elif reason == "not_in_workspace":
        from trailhead.gateway.command import NO_GATEWAY_OUTSIDE_WORKSPACE

        why = NO_GATEWAY_OUTSIDE_WORKSPACE
    else:
        step = message or NEXT_STEPS.get(reason)
        why = f"it is not registered ({reason})" + (f": {step}" if step else "")
    return _one_line(f"{NO_CREDENTIAL}: {why}")


def _log(reason: str, key: str | None, line: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        path = register_log()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(f"{stamp} {key or '-'} {reason} {line}\n")
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
        key = register(timeout=HOOK_TIMEOUT_SECONDS if args.hook else TIMEOUT_SECONDS)
    except Refused as refusal:
        reason, key, message = refusal.reason, refusal.key, refusal.message
    except Exception:  # noqa: BLE001 — a traceback must never carry the authority
        reason, key, message = UNEXPECTED_FAILURE, None, None
    else:
        if not args.hook:
            print(f"registered {key}")
        return 0
    line = no_credential_line(reason, message)
    _log(reason, key, line)
    print(reason, file=sys.stderr)
    if args.hook:
        print(
            json.dumps(
                {
                    "systemMessage": line,
                    "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": line},
                }
            )
        )
    return 0 if args.hook else 1
