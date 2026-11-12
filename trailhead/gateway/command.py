"""``outpost-gateway`` — the sandbox's way to reach Outpost's gateway routes.

``run list`` finds the harness this process runs under, reads that session's
gateway credential, exchanges it for a short-lived bearer token (kept in memory
only), and lists the workspace's runs. The session key comes only from the parent
process chain: there is no argument or environment variable that names one.

The secret and token are never printed, logged, written, exported or passed to a
subprocess; an unexpected failure reports its exception type and nothing else.
"""

from __future__ import annotations

import argparse
import json
import sys

from trailhead.gateway import outpost_http
from trailhead.gateway.register import register_log
from trailhead.harness import session_key
from trailhead.outpost_lifecycle import APP as OUTPOST_APP
from trailhead.outpost_lifecycle import DAEMON_PORT
from trailhead.paths import state_dir

SANDBOXED = "outpost-gateway is running inside the sandbox; run it on its own, not piped or chained"


class CommandError(Exception):
    """A one-line, human-readable reason the command could not finish."""


def _fail(message: str) -> CommandError:
    return CommandError(f"outpost-gateway: {message}")


def _read_credential(key: str) -> tuple[str, str]:
    path = state_dir(OUTPOST_APP) / "sessions" / key / "gateway-client.json"
    no_credential = _fail(f"this session has no gateway credential; see {register_log()} for why")
    try:
        raw = path.read_text()
    except FileNotFoundError:
        raise no_credential from None
    except OSError:
        raise CommandError(SANDBOXED) from None
    try:
        parsed = json.loads(raw)
        client_id, client_secret = parsed["client_id"], parsed["client_secret"]
    except (ValueError, KeyError, TypeError):
        raise no_credential from None
    if not isinstance(client_id, str) or not isinstance(client_secret, str):
        raise no_credential
    return client_id, client_secret


def _exchange(client_id: str, client_secret: str, port: int) -> str:
    reply = outpost_http.request(
        "POST",
        "/broker/token",
        headers={"Authorization": outpost_http.basic_auth(client_id, client_secret)},
        form={"grant_type": "client_credentials"},
        port=port,
    )
    token = reply.body.get("access_token")
    if reply.status == 200 and isinstance(token, str) and token:
        return token
    code = reply.body.get("error")
    if isinstance(code, str):
        raise _fail(f"Outpost refused the credential: {code}")
    raise _fail("Outpost answered with a reply this command cannot read")


def _list_runs(token: str, port: int) -> dict:
    reply = outpost_http.request(
        "GET", "/gateway/runs", headers={"Authorization": f"Bearer {token}"}, port=port
    )
    body = reply.body
    if reply.status == 200:
        runs = body.get("runs")
        if isinstance(body.get("workspace"), str) and isinstance(runs, list):
            return body
    elif reply.status in (401, 403) and isinstance(body.get("reason"), str):
        message = body.get("message")
        suffix = f": {message}" if isinstance(message, str) and message else ""
        raise _fail(f"{body['reason']}{suffix}")
    raise _fail("Outpost answered with a reply this command cannot read")


def _table(runs: list[dict]) -> str:
    rows = [["ID", "JOB", "STATE", "RESULT", "CREATED"]]
    for run in runs:
        rows.append(
            [str(run.get(k) if run.get(k) is not None else "-") for k in ("id", "job", "state", "result", "created_at")]
        )
    widths = [max(len(r[i]) for r in rows) for i in range(5)]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows)


def _run_list(as_json: bool, port: int) -> str:
    key = session_key()
    if key is None:
        raise CommandError(SANDBOXED)
    client_id, client_secret = _read_credential(key)
    try:
        token = _exchange(client_id, client_secret, port)
        listing = _list_runs(token, port)
    except outpost_http.OutpostUnreachable as err:
        raise _fail(f"Outpost is not reachable at {outpost_http.DAEMON_HOST}:{err.port}; is it running?") from None
    if as_json:
        return json.dumps({"workspace": listing["workspace"], "runs": listing["runs"]}, indent=2)
    if not listing["runs"]:
        return f"No runs in workspace {listing['workspace']}"
    return _table(listing["runs"])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="outpost-gateway",
        description="Reach Outpost's gateway from inside a sandboxed session. "
        "Run it on its own, not piped or chained.",
        epilog="commands:\n  run list [--json]   list the workspace's runs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="group", required=True)
    run = sub.add_parser("run", help="the workspace's CI runs").add_subparsers(dest="action", required=True)
    listing = run.add_parser("list", help="list the workspace's runs")
    listing.add_argument("--json", action="store_true", help="print JSON instead of a table")
    return parser


def main(argv: list[str] | None = None, *, port: int = DAEMON_PORT) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        output = _run_list(args.json, port)
    except CommandError as err:
        print(err, file=sys.stderr)
        return 1
    except Exception as err:  # noqa: BLE001 — a traceback must never carry credential material
        print(f"outpost-gateway: unexpected failure ({type(err).__name__})", file=sys.stderr)
        return 1
    print(output)
    return 0
