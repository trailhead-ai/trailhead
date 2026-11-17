"""The request directory and the request/result shape of the eval command.

This is the backend-neutral interface: ``portage eval start`` creates a request, a backend runs
it, ``portage eval wait`` reads it. A backend implements it by writing the files below. The local
backend (``local``) is the only one today.

A request is the directory ``<state_dir("portage")>/eval/<id>`` (mode 0700). ``<id>`` is the
backend name, ``-``, and 12 characters of ``[a-z0-9]``; anything else is never turned into a path.
Every file is written to a temporary name and renamed into place, so a reader sees a whole file or
none::

    request.json          {"schema_version": 1, "id", "backend", "repo", "plugin_root",
                           "cases": [{"case", "runs", "pass_at",
                                      "runner": {"model", "allow_tools", "max_cost_usd"}}, ...]}
    results/<case>.json   one case's result, written when that case is decided (below)
    result.json           {"schema_version": 1, "cases": [<case result>, ...]}, in request order,
                          written last and always: the request is finished exactly when it exists
    worker.pid, worker.log   the local backend's worker (not part of the interface)

A case result::

    {"case": str, "verdict": "pass" | "fail" | "not-run", "reason": null | <reason>,
     "next_step": null | "stop and report" | "resubmit", "passed": int, "failed": int,
     "errored": int, "runs": [{"passed": bool, "errored": bool, "score": number | null,
                               "graders": [{"name": str, "passed": bool}, ...]}, ...]}

``reason`` and ``next_step`` are set only for a not-run case. ``runner-broken`` (the canary did not
pass, or the worker crashed) and every reason the eval core gives are followed by ``stop and
report``; ``interrupted`` (the worker died before deciding the case) is followed by ``resubmit``.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import string
import sys
from pathlib import Path

from portage.eval_core.run import not_run
from portage.eval_gate.cache import ensure_private_dir
from portage.eval_gate.run import atomic_write
from trailhead.paths import state_dir

SCHEMA_VERSION = 1
ID_PATTERN = re.compile(r"^local-[a-z0-9]{12}\Z")
_ID_ALPHABET = string.ascii_lowercase + string.digits

RUNNER_BROKEN = "runner-broken"
INTERRUPTED = "interrupted"
STOP_AND_REPORT = "stop and report"
RESUBMIT = "resubmit"
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


def wait_command(request_id: str) -> str:
    return f"portage eval wait {request_id}"


def next_step_for(reason: str) -> str:
    return RESUBMIT if reason == INTERRUPTED else STOP_AND_REPORT


def not_run_case(case: str, runs: int, reason: str) -> dict:
    return {**not_run(case, runs, reason), "next_step": next_step_for(reason)}


def print_not_run_block(reason: str, *, indent: str = "", out=None) -> None:
    """The two lines every not-run outcome prints, whether from ``start`` or ``wait``."""
    out = out or sys.stdout
    print(f"{indent}not run: {reason}", file=out)
    print(f"{indent}next step: {next_step_for(reason)}", file=out)


def write_json(path: Path, doc: dict) -> None:
    atomic_write(path, json.dumps(doc, indent=2) + "\n")


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


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
