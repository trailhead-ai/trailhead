"""``outpost-gateway`` — the sandbox's way to reach Outpost's gateway routes.

Every verb finds the harness this process runs under, reads that session's
gateway credential, and exchanges it for a broker bearer token (kept in memory only).
A session with no credential file yet registers itself first, through the same step the
SessionStart hook runs. A token Outpost refuses as expired is exchanged again once and the
call retried. The session key comes only from the parent process chain: there is no
argument or environment variable that names one. Reaching Outpost has one overall budget
(``outpost_http.reach_budget``); text from Outpost is stripped of control characters before
it reaches the terminal.

``run start`` uploads the working tree and prints the new run's id; ``run watch`` follows
a run's log; ``run view`` and ``run list`` show runs (``run view --log`` prints a run's whole
log once, without following); ``run cancel`` asks Outpost to cancel a queued or running run.
Exit codes: 0 done, 1 (``watch --exit-status`` only) the job ran and failed or the run was
cancelled, 3 (``watch --exit-status`` only) the run did
not execute, 2 anything else that went wrong, including every refusal from Outpost, an
unreachable or silent Outpost, a dropped stream and a usage error.

The secret and token are never printed, logged, written, exported or passed to a
subprocess; an unexpected failure reports its exception type and nothing else.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import stat
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

from trailhead.gateway import outpost_http, register, tree_upload
from trailhead.harness import session_key
from trailhead.outpost_lifecycle import APP as OUTPOST_APP
from trailhead.outpost_lifecycle import DAEMON_PORT
from trailhead.paths import state_dir

STATES = ("queued", "in_progress", "completed")
RESULTS = ("success", "failure", "cancelled")
CANNOT_READ = "Outpost answered with a reply this command cannot read"
# Outpost answers a cancel once the run's stop is signalled; removing its containers goes on in
# the background. A cancel that gets no answer in this long is reported as requested, not as lost.
CANCEL_TIMEOUT_SECONDS = 10.0
NO_GATEWAY_OUTSIDE_WORKSPACE = (
    "this session is not in a camp workspace; sessions outside any camp workspace have no gateway"
)
SANDBOXED = "outpost-gateway is running inside the sandbox; run it on its own, not piped or chained"


class CommandError(Exception):
    """A one-line, human-readable reason the command could not finish."""


def _fail(message: str) -> CommandError:
    return CommandError(f"outpost-gateway: {message}")


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _clean(text: str) -> str:
    """*text* from Outpost without its C0 and C1 control characters, newline and tab kept, so it cannot drive the terminal."""
    return _CONTROL.sub("", text)


def _one_line(text: str) -> str:
    """*text* cleaned and fit for one table cell or field: newlines and tabs become spaces."""
    return _clean(text).replace("\n", " ").replace("\t", " ")


class _NoCredential(Exception):
    """The session has no usable credential file."""


def _operator_uid() -> int:
    return os.geteuid()


def _read_credential(key: str) -> tuple[str, str]:
    """The client id and secret in this session's credential file, which must be a regular file the
    operator owns with mode 0600, opened without following a link: anything else is refused before
    the contents are read."""
    path = state_dir(OUTPOST_APP) / "sessions" / key / "gateway-client.json"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise _NoCredential from None
    except OSError as err:
        if err.errno == errno.ELOOP:
            raise _fail(f"the gateway credential {path} is a symbolic link; refusing to read it") from None
        raise CommandError(SANDBOXED) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _fail(f"the gateway credential {path} is not a regular file; refusing to read it")
        if info.st_uid != _operator_uid():
            raise _fail(f"the gateway credential {path} is not owned by you; refusing to read it")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise _fail(
                f"the gateway credential {path} has mode {stat.S_IMODE(info.st_mode):04o}; it must be 0600, refusing to read it"
            )
        with os.fdopen(fd, "r") as handle:
            fd = -1
            raw = handle.read()
    except OSError:
        raise CommandError(SANDBOXED) from None
    finally:
        if fd != -1:
            os.close(fd)
    try:
        parsed = json.loads(raw)
        client_id, client_secret = parsed["client_id"], parsed["client_secret"]
    except (ValueError, KeyError, TypeError):
        raise _NoCredential from None
    if not isinstance(client_id, str) or not isinstance(client_secret, str):
        raise _NoCredential
    return client_id, client_secret


def _register(port: int) -> str:
    """Register this session through the registration step; its key, or the error saying why it is not registered."""
    try:
        return register.register(port=port, timeout=None)
    except register.Refused as refusal:
        if refusal.reason == register.OUTPOST_UNREACHABLE:
            raise outpost_http.OutpostUnreachable(port) from None
        if refusal.reason == "not_in_workspace":
            raise _fail(NO_GATEWAY_OUTSIDE_WORKSPACE) from None
        next_step = refusal.message or register.NEXT_STEPS.get(refusal.reason)
        suffix = f": {_clean(next_step)}" if next_step else ""
        raise _fail(f"this session is not registered: {_clean(refusal.reason)}{suffix}") from None


def _credential(key: str, port: int) -> tuple[str, str]:
    """The session's client id and secret, registering the session first when it has none."""
    try:
        return _read_credential(key)
    except _NoCredential:
        pass
    key = _register(port)
    try:
        return _read_credential(key)
    except _NoCredential:
        raise _fail("this session registered but Outpost left it no gateway credential") from None


def gateway_token(port: int) -> str:
    """A fresh broker bearer token for this session: its credential read (the session registered first if it has
    none) and exchanged. The one place a credential becomes a token."""
    key = session_key()
    if key is None:
        raise CommandError(SANDBOXED)
    client_id, client_secret = _credential(key, port)
    try:
        return _exchange(client_id, client_secret, port)
    except outpost_http.ReplyTimedOut:
        raise outpost_http.OutpostUnreachable(port) from None


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
    code, description = reply.body.get("error"), reply.body.get("error_description")
    if isinstance(code, str):
        detail = _clean(code)
        if isinstance(description, str) and description:
            reason, separator, message = description.partition(": ")
            detail = f"{_clean(reason)}: {_clean(message)}" if separator else f"{detail}: {_clean(description)}"
        raise _fail(f"Outpost refused the credential: {detail}")
    raise _fail(CANNOT_READ)


class _Gateway:
    """The gateway as one session sees it: a bearer token, replaced once when Outpost refuses it as expired."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.token = gateway_token(port)

    @staticmethod
    def _expired(reply: outpost_http.Reply | None) -> bool:
        return reply is not None and reply.status == 401 and reply.body.get("reason") == "credential_expired"

    def request(self, method: str, path: str, **kwargs) -> outpost_http.Reply:
        reply = outpost_http.request(method, path, headers=_bearer(self.token), port=self.port, **kwargs)
        if not self._expired(reply):
            outpost_http.reached()
            return reply
        self.token = gateway_token(self.port)
        body_file = kwargs.get("body_file")
        if body_file is not None:
            body_file[0].seek(0)
        reply = outpost_http.request(method, path, headers=_bearer(self.token), port=self.port, **kwargs)
        outpost_http.reached()
        return reply

    def open_stream(self, path: str) -> outpost_http.Stream:
        stream = outpost_http.open_stream(path, headers=_bearer(self.token), port=self.port)
        if not self._expired(stream.refusal):
            outpost_http.reached()
            return stream
        stream.close()
        self.token = gateway_token(self.port)
        stream = outpost_http.open_stream(path, headers=_bearer(self.token), port=self.port)
        outpost_http.reached()
        return stream


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
    cancel_reason = run.get("cancel_reason")
    if not (cancel_reason is None or isinstance(cancel_reason, str)):
        raise _fail(CANNOT_READ)
    return run


CANCEL_REASONS = {"session_ended": "the session that started it ended"}
"""What Outpost cancels a run for without its session asking, in words."""


def _result_text(run: dict) -> object:
    """The run's result, with why it was cancelled when Outpost cancelled it for a reason this command knows."""
    explanation = CANCEL_REASONS.get(run.get("cancel_reason"))
    if run["result"] == "cancelled" and explanation is not None:
        return f"cancelled: {explanation}"
    return run["result"]


def _get_view(gateway: _Gateway, run_id: int) -> dict:
    reply = gateway.request("GET", f"/gateway/runs/{run_id}")
    if reply.status != 200:
        raise _refusal(reply, run_id)
    return _known(reply.body)


def _list_runs(gateway: _Gateway) -> dict:
    reply = gateway.request("GET", "/gateway/runs")
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
    return _one_line(str(value)) if value is not None else "-"


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
    return ", ".join(f"{_one_line(str(k))}={_one_line(str(v))}" for k, v in matrix.items())


def _run_list(as_json: bool, port: int) -> int:
    listing = _list_runs(_Gateway(port))
    if as_json:
        print(_clean(json.dumps({"workspace": listing["workspace"], "runs": listing["runs"]}, indent=2)))
    elif not listing["runs"]:
        print(f"No runs in workspace {_one_line(listing['workspace'])}")
    else:
        print(_table(listing["runs"]))
    return 0


def _print_fields(fields: tuple[tuple[str, object], ...]) -> None:
    for name, value in fields:
        print(f"{name + ':':<9} {_cell(value)}")


def _run_view(run_id: int, port: int) -> int:
    run = _get_view(_Gateway(port), run_id)
    _print_fields((
        ("id", run_id),
        ("state", run["state"]),
        ("result", _result_text(run)),
        ("reason", run["reason"]),
        ("job", run.get("job")),
        ("workflow", run.get("workflow")),
    ))
    print(f"{'matrix:':<9} {_matrix_text(run.get('matrix'))}")
    return 0


def _emit(line: bytes) -> None:
    """Write a line of a run's log to stdout, as UTF-8 with control characters stripped."""
    sys.stdout.buffer.write(_clean(line.decode(errors="replace")).encode())


def _run_view_log(run_id: int, port: int) -> int:
    gateway = _Gateway(port)
    run = _get_view(gateway, run_id)
    if run["state"] == "queued":
        print(f"outpost-gateway: run {run_id} has not started, so it has no log yet", file=sys.stderr)
        return 0
    if run["state"] == "completed" and run.get("started_at") is None:
        print(f"outpost-gateway: run {run_id} never started, so it has no log", file=sys.stderr)
        return 0
    stream = gateway.open_stream(f"/gateway/runs/{run_id}/log")
    try:
        if stream.refusal is not None:
            raise _refusal(stream.refusal, run_id)
        try:
            for line in stream.lines():
                _emit(line)
        except outpost_http.StreamBroken:
            raise _fail(f"the log stream was dropped before the whole log of run {run_id} was read") from None
    finally:
        sys.stdout.buffer.flush()
        stream.close()
    return 0


def _run_cancel(run_id: int, port: int) -> int:
    gateway = _Gateway(port)
    try:
        reply = gateway.request("POST", f"/gateway/runs/{run_id}/cancel", timeout=CANCEL_TIMEOUT_SECONDS)
    except outpost_http.ReplyTimedOut:
        raise _fail(f"cancel requested; check run view {run_id}") from None
    if reply.status != 202:
        raise _refusal(reply)
    state, result = reply.body.get("state"), reply.body.get("result")
    if state not in STATES or result not in (None, "cancelled") or (state == "completed") != (result is not None):
        raise _fail(CANNOT_READ)
    _print_fields((("id", run_id), ("state", state), ("result", result)))
    print(f"cancel requested; run {run_id} will complete as cancelled")
    return 0


def _run_watch(run_id: int, exit_status: bool, port: int) -> int:
    gateway = _Gateway(port)
    written = 0
    while True:
        run = None
        stream = gateway.open_stream(f"/gateway/runs/{run_id}/log?follow=1")
        try:
            if stream.refusal is not None:
                raise _refusal(stream.refusal, run_id)
            position = 0
            try:
                for line in stream.lines():
                    end = position + len(line)
                    if end > written:
                        sys.stdout.write(_clean(line[max(0, written - position) :].decode(errors="replace")))
                        sys.stdout.flush()
                        written = end
                    position = end
            except outpost_http.StreamBroken as err:
                if not err.timed_out:
                    raise _fail(f"the log stream was dropped before run {run_id} completed") from None
                run = _get_view(gateway, run_id)
                if run["state"] != "completed":
                    continue
            break
        finally:
            stream.close()
    if run is None:
        run = _get_view(gateway, run_id)
    if run["state"] != "completed":
        raise _fail(f"the log stream ended before run {run_id} completed")
    if run["result"] == "cancelled":
        print(f"outpost-gateway: run {run_id} was {_result_text(run)}", file=sys.stderr)
        return 1 if exit_status else 0
    if not exit_status or run["result"] == "success":
        return 0
    if run["reason"] is None:
        return 1
    print(f"outpost-gateway: run {run_id} did not execute: {_clean(run['reason'])}", file=sys.stderr)
    return 3


def _run_start(job: str, workflow: str | None, port: int, scratch_home: Path | None) -> int:
    try:
        with tree_upload.top_level(Path(os.curdir), home=scratch_home) as tree:
            workflow = workflow or tree_upload.workflow_defining(tree, job)
            spool = tree.spool()
            try:
                tree_upload.write_tar(tree, spool)
            except BaseException:
                spool.close()
                raise
    except tree_upload.TreeError as err:
        raise _fail(str(err)) from None
    with spool:
        size = spool.tell()
        spool.seek(0)
        reply = _Gateway(port).request(
            "POST",
            f"/gateway/runs?{urlencode({'workflow': workflow, 'job': job})}",
            body_file=(spool, size, "application/x-tar"),
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
        "  run watch <id> [--exit-status]        follow a run's log (exit: 0 ok, 1 failed or cancelled, 3 not run)\n"
        "  run view <id> [--log]                 show a run, or print its whole log once\n"
        "  run cancel <id>                       cancel a queued or running run\n"
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
        "--exit-status",
        action="store_true",
        help="exit 0 on success, 1 on failure or cancelled, 3 if the run did not execute",
    )
    view = run.add_parser("view", help="show a run")
    view.add_argument("id", type=_run_id)
    view.add_argument("--log", action="store_true", help="print the run's whole log once, without following")
    cancel = run.add_parser("cancel", help="cancel a queued or running run")
    cancel.add_argument("id", type=_run_id)
    listing = run.add_parser("list", help="list the workspace's runs")
    listing.add_argument("--json", action="store_true", help="print JSON instead of a table")
    return parser


def _dispatch(args: argparse.Namespace, port: int, scratch_home: Path | None) -> int:
    if args.action == "start":
        return _run_start(args.job, args.workflow, port, scratch_home)
    if args.action == "watch":
        return _run_watch(args.id, args.exit_status, port)
    if args.action == "view":
        return _run_view_log(args.id, port) if args.log else _run_view(args.id, port)
    if args.action == "cancel":
        return _run_cancel(args.id, port)
    return _run_list(args.json, port)


def main(argv: list[str] | None = None, *, port: int = DAEMON_PORT, scratch_home: Path | None = None) -> int:
    """*scratch_home* is where ``run start`` makes its private upload folder; the default
    is the operator's home from the password database."""
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        with outpost_http.reach_budget():
            return _dispatch(args, port, scratch_home)
    except CommandError as err:
        print(err, file=sys.stderr)
    except outpost_http.OutpostUnreachable as err:
        print(
            _fail(f"Outpost is unreachable at {outpost_http.DAEMON_HOST}:{err.port}; is it running?"), file=sys.stderr
        )
    except Exception as err:  # noqa: BLE001 — a traceback must never carry credential material
        print(f"outpost-gateway: unexpected failure ({type(err).__name__})", file=sys.stderr)
    return 2
