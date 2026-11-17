"""The local backend: a detached worker process per request, and ``wait`` over the request directory.

``start_request`` creates the request (``request``), then spawns ``python -m
portage.eval_command.local <request dir>`` as its own session with stdin closed and stdout/stderr
in ``worker.log``, and records the pid in ``worker.pid`` (informational: liveness never reads it). The
worker is deliberately detached: the eval gate's no-background rule does not apply to it. It is
bounded by the per-case timeout.

Liveness is a lock, not a pid: ``start_request`` takes an exclusive ``flock`` on ``worker.lock`` in the
request directory before the spawn and the worker inherits that open file, so the lock is held from
before the worker's first instruction until the kernel releases it when the worker exits, however it
exits. ``wait`` asks for the lock without blocking: granted means the worker is gone, refused means it
is running. A recycled pid cannot hold it.

The worker runs the sandbox-write canary through the eval core once for the request, never cached;
when it does not pass every case is not run ``runner-broken`` and no case runner starts. Otherwise it
runs each case through the core with ``strict_tmp``, the runner temp base derived from ``HOME`` (never
``XDG_CACHE_HOME``), and writes ``results/<case>.json`` as each is decided. Whatever happens it ends by
writing ``result.json``: a crash leaves the unfinished cases not run ``runner-broken``, SIGTERM (which
also takes down the runner's process group, as the core's executor does on any exit) ``interrupted``.

``wait`` polls until ``result.json`` is a whole, valid file or the bound passes. A worker that is gone
with no valid ``result.json`` leaves its unfinished cases ``interrupted`` (exit 3, never 4). The
runner binary is validated by the gate's own ``PORTAGE_EVAL_RUNNER`` rules.
"""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path

from portage.eval_core.run import RUNNER_MISSING
from portage.eval_core import TmpBaseRefused, TmpPathTooLong, decide_case, execute, run_canary
from portage.eval_gate.run import _pin, _runner_override

from . import request as req

LOCK_NAME = "worker.lock"
CASE_TIMEOUT_SECONDS = 540
DEFAULT_WAIT_SECONDS = 110
MAX_WAIT_SECONDS = 540
POLL_SECONDS = 0.25
CLOCK = time.monotonic
SLEEP = time.sleep

_PASSED_CANARY = {"case": "sandbox-write", "verdict": "pass"}
_DROPPED_FROM_WORKER_ENV = ("XDG_CACHE_HOME", "SANDBOX_RUNTIME")


class StartFailed(Exception):
    """The request could not be created or its worker not spawned."""


class Terminated(BaseException):
    pass


def worker_env(environ=None) -> dict[str, str]:
    return {k: v for k, v in (os.environ if environ is None else environ).items()
            if k not in _DROPPED_FROM_WORKER_ENV}


def _take_lock(request_path: Path) -> int:
    fd = os.open(request_path / LOCK_NAME, os.O_WRONLY | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def start_request(*, repo: str, plugin_root: str, cases: list[dict]) -> str:
    """Create a request and spawn its detached worker; return the identifier."""
    try:
        request_id, path = req.create_request(
            {"repo": repo, "plugin_root": plugin_root, "cases": cases})
        log = open(path / "worker.log", "ab")
        lock = _take_lock(path)
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "portage.eval_command.local", str(path), str(lock)],
                env={**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)},
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                pass_fds=(lock,))
        finally:
            log.close()
            os.close(lock)
        (path / "worker.pid").write_text(f"{proc.pid}\n")
    except OSError as e:
        raise StartFailed(str(e)) from e
    return request_id


def _write_case(results: Path, result: dict) -> dict:
    result = {**result, "next_step": (result.get("next_step") or req.next_step_for(result["reason"])
                                      if result["verdict"] == "not-run" else None)}
    req.write_json(results / f"{result['case']}.json", result)
    return result


def run_worker(request_path: Path, *, env: dict[str, str] | None = None, execute=execute) -> None:
    """Run every case of the request and always write ``result.json``."""
    request_path = Path(request_path)
    doc = req.read_json(request_path / "request.json")
    cases = doc["cases"]
    results = request_path / "results"
    results.mkdir(mode=0o700, exist_ok=True)
    env = worker_env() if env is None else env
    done: dict[str, dict] = {}
    unfinished = req.RUNNER_BROKEN
    try:
        _run_cases(request_path, doc, results, env, execute, done)
    except Terminated:
        unfinished = req.INTERRUPTED
    except Exception:
        traceback.print_exc()
    final = [done[c["case"]] if c["case"] in done
             else req.not_run_case(c["case"], c["runs"], unfinished) for c in cases]
    req.write_json(request_path / "result.json", {"schema_version": req.SCHEMA_VERSION, "cases": final})


def _run_cases(request_path, doc, results, env, execute_fn, done) -> None:
    common = dict(execute=execute_fn, resolve_runner=_runner_override, pin=_pin, env=env,
                  strict_tmp=True)
    plugin_dir = Path(doc["repo"]) / doc["plugin_root"]

    def broken_all(why: str, next_step: str | None = None) -> None:
        print(f"eval worker: {why}", file=sys.stderr)
        for c in doc["cases"]:
            done[c["case"]] = _write_case(
                results, req.not_run_case(c["case"], c["runs"], req.RUNNER_BROKEN, next_step))

    try:
        canary = run_canary(work_dir=request_path, timeout_seconds=CASE_TIMEOUT_SECONDS, **common)
    except (TmpPathTooLong, TmpBaseRefused) as e:
        return broken_all(str(e))
    if canary["verdict"] != "pass":
        return broken_all(f"the canary did not pass ({canary['verdict']}, {canary['reason']})",
                          req.RUN_TRAILHEAD_UPDATE if canary["reason"] == RUNNER_MISSING else None)
    for c in doc["cases"]:
        try:
            result = decide_case(
                plugin_dir=plugin_dir, case=c["case"], runs=c["runs"], pass_at=c["pass_at"],
                runner=c["runner"], output_dir=request_path / "runs",
                timeout_seconds=CASE_TIMEOUT_SECONDS, work_dir=request_path,
                canary=lambda run, timeout: _PASSED_CANARY, label="eval worker", **common)
        except (TmpPathTooLong, TmpBaseRefused) as e:
            print(f"eval worker: {e}", file=sys.stderr)
            result = req.not_run_case(c["case"], c["runs"], req.RUNNER_BROKEN)
        done[c["case"]] = _write_case(results, result)


def _on_sigterm(signum, frame):
    raise Terminated()


def main(argv: list[str]) -> int:
    signal.signal(signal.SIGTERM, _on_sigterm)
    if len(argv) > 1:
        os.set_inheritable(int(argv[1]), False)
    else:
        _take_lock(Path(argv[0]))
    run_worker(Path(argv[0]))
    return 0


def _worker_alive(request_path: Path) -> bool:
    try:
        fd = os.open(request_path / LOCK_NAME, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


def _finished(request_path: Path) -> list[dict] | None:
    doc = req.read_json(request_path / "result.json")
    return doc["cases"] if req.valid_result(doc) else None


def _case_files(request_path: Path, cases: list[dict]) -> dict[str, dict]:
    found = {}
    for c in cases:
        doc = req.read_json(request_path / "results" / f"{c['case']}.json")
        if req.valid_case_result(doc) and doc["case"] == c["case"]:
            found[c["case"]] = doc
    return found


def _summary(cases: list[dict]) -> str:
    counts = {v: sum(1 for c in cases if c["verdict"] == v) for v in req.VERDICTS}
    parts = [(counts["fail"], "failed"), (counts["not-run"], "not run"), (counts["pass"], "passed")]
    return ", ".join(f"{n} {label}" for n, label in parts if n)


def _score(run: dict) -> str:
    if run.get("errored"):
        return "errored"
    status = "passed" if run.get("passed") else "failed"
    return f"score {run.get('score')} {status}"


def _render(request_id: str, cases: list[dict], out) -> int:
    print(f"request: {request_id}", file=out)
    print(f"summary: {_summary(cases)}", file=out)
    for c in cases:
        label = {"pass": "pass", "fail": "fail", "not-run": "not run"}[c["verdict"]]
        print(f"case {c['case']}: {label}", file=out)
        if c["verdict"] == "not-run":
            req.print_not_run_block(c.get("reason") or req.RUNNER_BROKEN, indent="  ", out=out,
                                    next_step=c.get("next_step"))
        for i, run in enumerate(c["runs"], 1):
            print(f"  run {i}: {_score(run)}", file=out)
    if any(c["verdict"] == "fail" for c in cases):
        return 1
    return 3 if any(c["verdict"] == "not-run" for c in cases) else 0


def wait(request_id: str, timeout_seconds: int, *, out=None, clock=None, sleep=None) -> int:
    """Report a request: 0 all passed, 1 a case failed, 3 none failed and one not run, 4 still
    running when the bound passed. Raises ``Refused`` (exit 2) for an off-shape or unknown id."""
    out = out or sys.stdout
    clock, sleep = clock or CLOCK, sleep or SLEEP
    path = req.request_dir(request_id)
    doc = req.read_json(path / "request.json")
    if not path.is_dir() or not isinstance(doc, dict) or not isinstance(doc.get("cases"), list):
        raise req.Refused(f"no request {request_id}")
    cases = doc["cases"]
    deadline = clock() + timeout_seconds
    while True:
        finished = _finished(path)
        if finished is None and not _worker_alive(path):
            finished = _finished(path)
        if finished is not None:
            return _render(request_id, finished, out)
        have = _case_files(path, cases)
        if not _worker_alive(path):
            return _render(request_id, [have.get(c["case"]) or req.not_run_case(
                c["case"], c["runs"], req.INTERRUPTED) for c in cases], out)
        if clock() >= deadline:
            names = ", ".join(c["case"] for c in cases if c["case"] in have)
            print(f"request: {request_id}", file=out)
            print(f"running: {len(have)} of {len(cases)} cases finished"
                  + (f" ({names})" if names else ""), file=out)
            print(f"wait again: {req.wait_command(request_id)}", file=out)
            return 4
        sleep(max(0.0, min(POLL_SECONDS, deadline - clock())))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
