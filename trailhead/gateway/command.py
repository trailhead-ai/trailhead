"""``outpost-gateway`` — the sandbox's way to reach Outpost's gateway routes.

Every verb finds the harness this process runs under, reads that session's
gateway credential, and exchanges it for a broker bearer token (kept in memory only;
the broker does not expire its tokens). The session key comes only from the parent
process chain: there is no argument or environment variable that names one.

``run start`` uploads the working tree and prints the new run's id; ``run watch`` follows
a run's log; ``run view`` and ``run list`` show runs. Exit codes: 0 done, 1 (``watch
--exit-status`` only) the job ran and failed, 3 (``watch --exit-status`` only) the run did
not execute, 2 anything else that went wrong, including every refusal from Outpost, an
unreachable or silent Outpost, a dropped stream and a usage error.

The secret and token are never printed, logged, written, exported or passed to a
subprocess; an unexpected failure reports its exception type and nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from trailhead.gateway import outpost_http, tree_upload
from trailhead.gateway.register import register_log
from trailhead.harness import session_key
from trailhead.outpost_lifecycle import APP as OUTPOST_APP
from trailhead.outpost_lifecycle import DAEMON_PORT
from trailhead.paths import state_dir

STATES = ("queued", "in_progress", "completed")
RESULTS = ("success", "failure")
CANNOT_READ = "Outpost answered with a reply this command cannot read"
SANDBOXED = "outpost-gateway is running inside the sandbox; run it on its own, not piped or chained"


class CommandError(Exception):
    """A one-line, human-readable reason the command could not finish."""


def _fail(message: str) -> CommandError:
    return CommandError(f"outpost-gateway: {message}")


def _clean(text: str) -> str:
    """*text* from Outpost with control characters replaced, so it cannot drive the terminal."""
    return re.sub(r"[\x00-\x1f\x7f]", "?", text)


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


def _token(port: int) -> str:
    key = session_key()
    if key is None:
        raise CommandError(SANDBOXED)
    client_id, client_secret = _read_credential(key)
    return _exchange(client_id, client_secret, port)


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
    raise _fail(CANNOT_READ)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _refusal(reply: outpost_http.Reply, run_id: int | None = None) -> CommandError:
    """The error for a non-2xx reply: Outpost's reason and message, or "no such run" for a 404 on *run_id*."""
    reason, message = reply.body.get("reason"), reply.body.get("message")
    if reply.status == 404 and reason == "run_not_found" and run_id is not None:
        return _fail(f"no run {run_id} in this workspace")
    if isinstance(reason, str) and reason:
        suffix = f": {_clean(message)}" if isinstance(message, str) and message else ""
        return _fail(f"{_clean(reason)}{suffix}")
    return _fail(CANNOT_READ)


def _known(run: object) -> dict:
    """*run* if its state, result and reason are values this command knows, else the unreadable-reply error."""
    if not isinstance(run, dict):
        raise _fail(CANNOT_READ)
    state, result, reason = run.get("state"), run.get("result"), run.get("reason")
    if state not in STATES or (result is not None and result not in RESULTS):
        raise _fail(CANNOT_READ)
    if (state == "completed") != (result is not None) or not (reason is None or isinstance(reason, str)):
        raise _fail(CANNOT_READ)
    return run


def _get_view(token: str, run_id: int, port: int) -> dict:
    reply = outpost_http.request("GET", f"/gateway/runs/{run_id}", headers=_bearer(token), port=port)
    if reply.status != 200:
        raise _refusal(reply, run_id)
    return _known(reply.body)


def _list_runs(token: str, port: int) -> dict:
    reply = outpost_http.request("GET", "/gateway/runs", headers=_bearer(token), port=port)
    if reply.status != 200:
        raise _refusal(reply)
    body = reply.body
    runs = body.get("runs")
    if not isinstance(body.get("workspace"), str) or not isinstance(runs, list):
        raise _fail(CANNOT_READ)
    for run in runs:
        _known(run)
    return body


def _cell(value: object) -> str:
    return _clean(str(value)) if value is not None else "-"


def _table(runs: list[dict]) -> str:
    keys = ("id", "job", "state", "result", "reason", "created_at")
    rows = [["ID", "JOB", "STATE", "RESULT", "REASON", "CREATED"]]
    for run in runs:
        rows.append([_cell(run.get(k)) for k in keys])
    widths = [max(len(r[i]) for r in rows) for i in range(len(keys))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows)


def _matrix_text(matrix: object) -> str:
    if not isinstance(matrix, dict) or not matrix:
        return "-"
    return ", ".join(f"{_clean(str(k))}={_clean(str(v))}" for k, v in matrix.items())


def _run_list(as_json: bool, port: int) -> int:
    listing = _list_runs(_token(port), port)
    if as_json:
        print(json.dumps({"workspace": listing["workspace"], "runs": listing["runs"]}, indent=2))
    elif not listing["runs"]:
        print(f"No runs in workspace {_clean(listing['workspace'])}")
    else:
        print(_table(listing["runs"]))
    return 0


def _run_view(run_id: int, port: int) -> int:
    run = _get_view(_token(port), run_id, port)
    fields = (
        ("id", run_id),
        ("state", run["state"]),
        ("result", run["result"]),
        ("reason", run["reason"]),
        ("job", run.get("job")),
        ("workflow", run.get("workflow")),
    )
    for name, value in fields:
        print(f"{name + ':':<9} {_cell(value)}")
    print(f"{'matrix:':<9} {_matrix_text(run.get('matrix'))}")
    return 0


def _run_watch(run_id: int, exit_status: bool, port: int) -> int:
    token = _token(port)
    written = 0
    while True:
        run = None
        stream = outpost_http.open_stream(f"/gateway/runs/{run_id}/log?follow=1", headers=_bearer(token), port=port)
        try:
            if stream.refusal is not None:
                raise _refusal(stream.refusal, run_id)
            position = 0
            try:
                for line in stream.lines():
                    end = position + len(line)
                    if end > written:
                        sys.stdout.write(line[max(0, written - position) :].decode(errors="replace"))
                        sys.stdout.flush()
                        written = end
                    position = end
            except outpost_http.StreamBroken as err:
                if not err.timed_out:
                    raise _fail(f"the log stream was dropped before run {run_id} completed") from None
                run = _get_view(token, run_id, port)
                if run["state"] != "completed":
                    continue
            break
        finally:
            stream.close()
    if run is None:
        run = _get_view(token, run_id, port)
    if run["state"] != "completed":
        raise _fail(f"the log stream ended before run {run_id} completed")
    if not exit_status or run["result"] == "success":
        return 0
    if run["reason"] is None:
        return 1
    print(f"outpost-gateway: run {run_id} did not execute: {_clean(run['reason'])}", file=sys.stderr)
    return 3


def _run_start(job: str, workflow: str | None, port: int) -> int:
    with tempfile.TemporaryFile() as spool:
        try:
            with tree_upload.top_level(Path(os.curdir)) as tree:
                workflow = workflow or tree_upload.workflow_defining(tree, job)
                tree_upload.write_tar(tree, spool)
        except tree_upload.TreeError as err:
            raise _fail(str(err)) from None
        size = spool.tell()
        spool.seek(0)
        token = _token(port)
        reply = outpost_http.request(
            "POST",
            f"/gateway/runs?{urlencode({'workflow': workflow, 'job': job})}",
            headers=_bearer(token),
            body_file=(spool, size, "application/x-tar"),
            port=port,
        )
    body = reply.body
    if reply.status != 202:
        raise _refusal(reply)
    run_id, matrix, not_run = body.get("id"), body.get("matrix"), body.get("not_run")
    if (
        isinstance(run_id, bool)
        or not isinstance(run_id, int)
        or not (matrix is None or isinstance(matrix, dict))
        or isinstance(not_run, bool)
        or not isinstance(not_run, int)
    ):
        raise _fail(CANNOT_READ)
    print(run_id)
    if matrix:
        print(f"outpost-gateway: matrix combination: {_matrix_text(matrix)}", file=sys.stderr)
    if not_run > 0:
        print(f"outpost-gateway: {not_run} other matrix combinations not run", file=sys.stderr)
    return 0


def _run_id(text: str) -> int:
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError(f"{text!r} is not a run id")
    return int(text)


def _parser() -> argparse.ArgumentParser:
    shim = state_dir("trailhead") / "bin" / "outpost-gateway"
    parser = argparse.ArgumentParser(
        prog="outpost-gateway",
        description="Reach Outpost's gateway from inside a sandboxed session. Run it on its own, not piped or chained.",
        epilog="commands:\n"
        "  run start <job> [--workflow <path>]   upload the working tree and start a run; prints its id\n"
        "  run watch <id> [--exit-status]        follow a run's log (exit: 0 ok, 1 failed, 3 not run)\n"
        "  run view <id>                         show a run\n"
        "  run list [--json]                     list the workspace's runs\n\n"
        f"run it as: {shim} run list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="group", required=True)
    run = sub.add_parser("run", help="the workspace's CI runs").add_subparsers(dest="action", required=True)
    start = run.add_parser("start", help="upload the working tree and start a run")
    start.add_argument("job")
    start.add_argument(
        "--workflow", help="workflow file; default: the only one under .github/workflows/ defining the job"
    )
    watch = run.add_parser("watch", help="follow a run's log")
    watch.add_argument("id", type=_run_id)
    watch.add_argument(
        "--exit-status", action="store_true", help="exit 0 on success, 1 on failure, 3 if the run did not execute"
    )
    view = run.add_parser("view", help="show a run")
    view.add_argument("id", type=_run_id)
    listing = run.add_parser("list", help="list the workspace's runs")
    listing.add_argument("--json", action="store_true", help="print JSON instead of a table")
    return parser


def _dispatch(args: argparse.Namespace, port: int) -> int:
    if args.action == "start":
        return _run_start(args.job, args.workflow, port)
    if args.action == "watch":
        return _run_watch(args.id, args.exit_status, port)
    if args.action == "view":
        return _run_view(args.id, port)
    return _run_list(args.json, port)


def main(argv: list[str] | None = None, *, port: int = DAEMON_PORT) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return _dispatch(args, port)
    except CommandError as err:
        print(err, file=sys.stderr)
    except outpost_http.OutpostUnreachable as err:
        print(
            _fail(f"Outpost is not reachable at {outpost_http.DAEMON_HOST}:{err.port}; is it running?"), file=sys.stderr
        )
    except Exception as err:  # noqa: BLE001 — a traceback must never carry credential material
        print(f"outpost-gateway: unexpected failure ({type(err).__name__})", file=sys.stderr)
    return 2
