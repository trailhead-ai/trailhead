"""The request directory and the request/result shape of the eval command.

This is the backend-neutral interface: ``portage eval start`` creates a request, a backend runs
it, ``portage eval wait`` reads it. A backend implements it by writing the files below. The local
backend (``local``) is the only one today.

A request is the directory ``<state_dir("portage")>/eval/<id>`` (mode 0700). ``<id>`` is the
backend name, ``-``, and 12 characters of ``[a-z0-9]``; anything else is never turned into a path.
Every file is written to a temporary name and renamed into place, so a reader sees a whole file or
none::

    request.json          {"schema_version": 1, "id", "backend", "repo", "plugin_root",
                           "started_in_sandbox": bool (the sandbox marker was set when it started),
                           "cases": [{"case", "runs", "pass_at",
                                      "runner": {"model", "allow_tools", "max_cost_usd"}}, ...]}
    results/<case>.json   one case's result, written when that case is decided (below)
    result.json           {"schema_version": 1, "cases": [<case result>, ...]}, in request order,
                          written last and always: the request is finished exactly when it exists
    worker.pid, worker.log, worker.lock   the local backend's worker (not part of the interface)

A case result::

    {"case": str, "verdict": "pass" | "fail" | "not-run", "reason": null | <reason>,
     "next_step": null | "stop and report" | "resubmit", "cause": str (optional), "passed": int, "failed": int,
     "errored": int, "runs": [{"passed": bool, "errored": bool, "score": number | null,
                               "graders": [{"name": str, "passed": bool}, ...],
                               "duration_seconds": number | null, "cost_usd": number | null,
                               "judge_cost_usd": number | null}, ...]}

The three timing fields are the eval core's validated numbers (null when unusable) and are never
read by this module. ``reason`` and ``next_step`` are set only for a not-run case, and ``next_step`` is always one of
``stop and report``, ``resubmit`` (and ``retry later``, which no not-run case carries). ``runner-broken``
(the canary did not pass, or the worker crashed) and every reason the eval core gives are followed by
``stop and report``, except the core's ``timeout``; ``interrupted`` (the worker died before deciding
the case) and ``timeout`` are followed by ``resubmit``. A ``runner-broken`` whose pinned runner binary
is gone also carries a ``cause`` naming the update that restores it, printed as a ``cause:`` line.
``eval-command-too-old`` is printed by ``start`` alone, when ``--require-version N`` names a version
above ``EVAL_COMMAND_VERSION`` (no request is created); it is followed by ``stop and report`` and every
reason in ``STOP_AND_REPORT_REASONS``, the tuple that enumerates the known stop-and-report reasons, is;
``next_step_for`` resubmits only the reasons in ``RESUBMIT_REASONS``, so any reason not in that set, one
the module has never heard of included, is ``stop and report``. ``could-not-leave-sandbox`` is printed
by ``start`` alone (no request exists), or by ``wait`` for a request started from inside the sandbox
whose worker is gone.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import string
import sys
from pathlib import Path

from portage.eval_core.run import (COST_CAP, NO_RUNNER, RUNNER_ERROR, RUNNER_MISSING, RUNS_ERRORED,
                                    SANDBOX_UNWRITABLE, TIMEOUT, not_run)
from portage.eval_gate.cache import ensure_private_dir
from portage.eval_gate.run import atomic_write
from trailhead.paths import PathResolutionError, state_dir

SCHEMA_VERSION = 1
EVAL_COMMAND_VERSION = 2
REQUIRE_VERSION_PATTERN = re.compile(r"^[0-9]{1,6}\Z")
ID_PATTERN = re.compile(r"^local-[a-z0-9]{12}\Z")
_ID_ALPHABET = string.ascii_lowercase + string.digits

RUNNER_BROKEN = "runner-broken"
INTERRUPTED = "interrupted"
COULD_NOT_LEAVE_SANDBOX = "could-not-leave-sandbox"
EVAL_COMMAND_TOO_OLD = "eval-command-too-old"
STOP_AND_REPORT = "stop and report"
RESUBMIT = "resubmit"
RUNNER_MISSING_CAUSE = ("the pinned runner binary is gone; put `claude` on PATH, then run "
                        "`trailhead update`")
STOP_AND_REPORT_REASONS = (RUNNER_BROKEN, COULD_NOT_LEAVE_SANDBOX, EVAL_COMMAND_TOO_OLD, NO_RUNNER,
                           RUNNER_MISSING, COST_CAP, RUNNER_ERROR, RUNS_ERRORED, SANDBOX_UNWRITABLE)
RESUBMIT_REASONS = (INTERRUPTED, TIMEOUT)
NEXT_STEPS = (STOP_AND_REPORT, RESUBMIT)
SHIM_ENV = "PORTAGE_EVAL_SHIM"
SHIM_NAME = "portage-eval"
VERDICTS = ("pass", "fail", "not-run")


class Refused(Exception):
    """A request the command will not act on (exit 2)."""


def valid_request_id(value: object) -> bool:
    return isinstance(value, str) and bool(ID_PATTERN.match(value))


def eval_root() -> Path:
    return state_dir("portage") / "eval"


def request_dir(request_id: str) -> Path:
    """The directory of a request; refuses an identifier of the wrong shape without touching disk."""
    if not valid_request_id(request_id):
        raise Refused(f"{request_id!r} is not a request identifier (local-<12 lowercase letters or digits>)")
    return eval_root() / request_id


def shim_from_env() -> str | None:
    """The absolute path of the pinned shim this process was run through, ``None`` when it was not
    run through one (or the value is not a plain absolute path)."""
    value = os.environ.get(SHIM_ENV)
    if not value or not os.path.isabs(value) or any(ch in value for ch in "\n\r\0"):
        return None
    return value


def default_shim() -> str | None:
    """Where ``trailhead install`` puts the pinned shim on this host."""
    try:
        return str(state_dir("trailhead") / "bin" / SHIM_NAME)
    except PathResolutionError:
        return None


def wait_command(request_id: str) -> str:
    shim = shim_from_env()
    return f"{shlex.quote(shim)} wait {request_id}" if shim else f"portage eval wait {request_id}"


def next_step_for(reason: str) -> str:
    return RESUBMIT if reason in RESUBMIT_REASONS else STOP_AND_REPORT


def not_run_case(case: str, runs: int, reason: str, next_step: str | None = None,
                 cause: str | None = None) -> dict:
    doc = {**not_run(case, runs, reason), "next_step": next_step or next_step_for(reason)}
    return {**doc, "cause": cause} if cause else doc


def print_not_run_block(reason: str, *, indent: str = "", out=None, next_step: str | None = None,
                        cause: str | None = None) -> None:
    """The lines every not-run outcome prints, whether from ``start`` or ``wait``: the reason, the next
    step, and a ``cause:`` line when one was recorded.

    *next_step* (a recorded one, when it is a known step) replaces the one the reason implies."""
    out = out or sys.stdout
    print(f"{indent}not run: {reason}", file=out)
    print(f"{indent}next step: {next_step if next_step in NEXT_STEPS else next_step_for(reason)}", file=out)
    if isinstance(cause, str) and cause:
        print(f"{indent}cause: {cause}", file=out)


def write_json(path: Path, doc: dict) -> None:
    atomic_write(path, json.dumps(doc, indent=2) + "\n")


def valid_case_result(doc: object) -> bool:
    return (isinstance(doc, dict) and isinstance(doc.get("case"), str)
            and doc.get("verdict") in VERDICTS
            and isinstance(doc.get("runs"), list))


def valid_result(doc: object) -> bool:
    return (isinstance(doc, dict) and isinstance(doc.get("cases"), list)
            and bool(doc["cases"]) and all(valid_case_result(c) for c in doc["cases"]))


def create_request(doc: dict) -> tuple[str, Path]:
    """Create the 0700 request directory and write ``request.json`` into it."""
    root = eval_root()
    try:
        ensure_private_dir(root, "eval request directory")
    except ValueError as e:
        raise OSError(str(e)) from e
    while True:
        request_id = "local-" + "".join(secrets.choice(_ID_ALPHABET) for _ in range(12))
        path = root / request_id
        try:
            os.mkdir(path, 0o700)
            break
        except FileExistsError:
            continue
    write_json(path / "request.json",
               {"schema_version": SCHEMA_VERSION, "id": request_id, "backend": "local", **doc})
    return request_id, path
