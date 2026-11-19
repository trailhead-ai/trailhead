"""Run one eval case through a harness's eval runner and decide it.

The request-driven part of ``portage eval-gate run``: a runner call in its own process group,
batched deciding over ``runs`` and ``pass_at``, the cost and wall-clock budget between batches, the
sandbox-write canary, and a private temp directory per call. The eval gate's gate-only checks live
in ``portage.eval_gate.run``, which calls this.

``decide_case`` returns, for one case::

    {"case": str, "verdict": "pass" | "fail" | "not-run", "passed": int, "failed": int,
     "errored": int, "reason": null | <reason code>,
     "runs": [{"passed": bool, "errored": bool, "score": number | null,
               "graders": [{"name": str, "passed": bool}, ...],
               "duration_seconds": number | null, "cost_usd": number | null,
               "judge_cost_usd": number | null}, ...]}

``runs`` holds every call's runs in order. The only runner values a run record carries are the
score, the graders' names and pass flags, and the run's ``durationSeconds``, ``costUsd`` and
``judgeCostUsd`` as ``duration_seconds``, ``cost_usd`` and ``judge_cost_usd``. Each of those
three lands only when it is an int or float (not a bool), finite, non-negative and at most 3600
(a duration) or 100 (a cost); anything else, a missing key included, is null. A run the runner
never returned, and every run of a runner killed at its time limit, carries null for all three.
``run_canary`` returns the same shape for case ``sandbox-write``.

The process executor and the runner resolution are injected, so a caller keeps its own:

- ``execute(runner, cwd, timeout) -> (exit_code | None, killed, missing)``; ``execute`` here is
  the default.
- ``resolve_runner(env)`` returns the validated runner-binary override, ``None`` when there is none,
  ``False`` when it is unusable (the case is then not-run ``runner-missing`` and nothing starts).
- ``pin(runner, override)`` returns the runner pointed at the override.

Each runner call gets a private temp directory under ``$XDG_CACHE_HOME/pegt`` (``$HOME/.cache/pegt``),
removed when the call ends. A per-call path over 51 characters raises ``TmpPathTooLong``; with
``strict_tmp`` a base under ``/tmp`` raises ``TmpBaseRefused``. Both are raised before any runner
starts, the canary included. A base that is a symlink, owned by another user or writable by group or
world raises ``TmpBaseRefused`` too.

Deciding reads the runner's JSON, never its exit code: the run list is ``cases[0].arms.with``. A run
is errored when ``error`` is set or ``skippedPaidGraders`` is true; runs requested but absent count as
errored. A scored run passes at score 1.0. Over ``n`` runs and ``pass_at`` k: pass when passing >= k;
fail when scored failing > n - k; otherwise nothing is decidable and the reason is ``timeout`` (the
runner was killed), ``cost-cap`` (partial, ``cost_ceiling``), ``runner-error`` (``auth_failed``
partial, or a non-zero exit with no usable JSON), else ``runs-errored``.

A case is decided in batches: the first call asks for ``min(k, n - k + 1)`` runs, the fewest that
could decide it; while neither verdict is reached the next asks for the smallest batch that could
still decide it, ``min(k - passing, n - k + 1 - failing)``, capped at the runs not yet asked. A later
call starts only when the last call added a scored run, the case's ``max_cost_usd`` minus the
``costUsd`` reported so far is above zero (a missing, non-numeric or negative ``costUsd`` counts as
the whole cap spent; the remainder is that call's ``--max-cost-usd``), and the wall-clock budget left
(the timeout minus the time since the start, canary included) is at least the first call's elapsed
time. The first call's result is at ``<output_dir>/<case>.json`` and its artifacts in
``<output_dir>/<case>/``; later calls write ``<output_dir>/<case>/batch-<runs asked before it>.json``.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from portage.eval_gate import canary
from portage.eval_gate.cache import ensure_private_dir

NO_RUNNER = "harness-has-no-eval-runner"
RUNNER_MISSING = "runner-missing"
TIMEOUT = "timeout"
COST_CAP = "cost-cap"
RUNNER_ERROR = "runner-error"
RUNS_ERRORED = "runs-errored"
SANDBOX_UNWRITABLE = "runner-sandbox-unwritable"
RUNNER_OVERRIDE_ENV = "PORTAGE_EVAL_RUNNER"

MAX_TMPDIR_CHARS = 51
_CALL_DIR_CHARS = 8
_TMP_ROOTS = ("/tmp", "/private/tmp")


class TmpBaseRefused(Exception):
    pass


class TmpPathTooLong(Exception):
    pass


def _tmp_base_path(env: dict[str, str]) -> Path:
    xdg = env.get("XDG_CACHE_HOME")
    if xdg and os.path.isabs(xdg):
        cache = Path(xdg)
    else:
        home = env.get("HOME")
        if not home or not os.path.isabs(home):
            raise TmpBaseRefused("no usable XDG_CACHE_HOME or HOME for the runner temp directory")
        cache = Path(home) / ".cache"
    return cache / "pegt"


def _under_tmp(path: Path) -> bool:
    resolved = os.path.realpath(path)
    return any(resolved == root or resolved.startswith(root + os.sep) for root in _TMP_ROOTS)


@contextmanager
def _call_tmpdir(env: dict[str, str], strict_tmp: bool = False):
    """A private directory for one runner call, removed on exit."""
    base = _tmp_base_path(env)
    if strict_tmp and _under_tmp(base):
        raise TmpBaseRefused(f"runner temp base {base} is under /tmp, where the runner's sandbox "
                             f"could not write; set XDG_CACHE_HOME outside /tmp")
    try:
        base = ensure_private_dir(base, "runner temp base")
    except ValueError as e:
        raise TmpBaseRefused(str(e)) from e
    length = len(str(base)) + 1 + _CALL_DIR_CHARS
    if length > MAX_TMPDIR_CHARS:
        raise TmpPathTooLong(
            f"runner temp path would be {length} characters, over the {MAX_TMPDIR_CHARS} the "
            f"runner's sandbox wrote through; set a shorter XDG_CACHE_HOME")
    path = Path(tempfile.mkdtemp(prefix="", dir=base))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _runs_of(doc: object) -> list[dict] | None:
    try:
        runs = doc["cases"][0]["arms"]["with"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(runs, list) or not all(isinstance(r, dict) for r in runs):
        return None
    return runs


def _is_errored(run: dict) -> bool:
    return run.get("error") is not None or bool(run.get("skippedPaidGraders"))


def _passes(run: dict) -> bool:
    score = run.get("score")
    return isinstance(score, (int, float)) and score >= 1.0


MAX_DURATION_SECONDS = 3600
MAX_RUN_COST_USD = 100
TIMING_FIELDS = (("duration_seconds", "durationSeconds", MAX_DURATION_SECONDS),
                 ("cost_usd", "costUsd", MAX_RUN_COST_USD),
                 ("judge_cost_usd", "judgeCostUsd", MAX_RUN_COST_USD))


def _measure(value: object, bound: float) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if 0 <= value <= bound else None


def _unmeasured(record: dict) -> dict:
    return {**record, **{field: None for field, _, _ in TIMING_FIELDS}}


def _run_record(run: dict) -> dict:
    graders = run.get("graders") if isinstance(run.get("graders"), list) else []
    score = run.get("score")
    return {
        "passed": not _is_errored(run) and _passes(run),
        "errored": _is_errored(run),
        "score": score if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
        "graders": [
            {"name": g["name"], "passed": bool(g.get("passed"))}
            for g in graders
            if isinstance(g, dict) and isinstance(g.get("name"), str)
        ],
        **{field: _measure(run.get(key), bound) for field, key, bound in TIMING_FIELDS},
    }


def _decide(doc: object, n: int, pass_at: int, exit_code: int | None, killed: bool,
            asked: int | None = None, killed_from: int = 0) -> dict:
    """The verdict over ``n`` runs; ``asked`` is how many of them have been requested so far.

    When ``killed``, the runs from index ``killed_from`` on came from the call that was killed
    and carry no timing; earlier runs came from calls that finished and keep theirs."""
    asked = n if asked is None else asked
    runs = _runs_of(doc)
    records = [_unmeasured(rec) if killed and i >= killed_from else rec
               for i, rec in enumerate(_run_record(r) for r in (runs or [])[:asked])]
    scored = [r for r in (runs or [])[:asked] if not _is_errored(r)]
    passing = sum(1 for r in scored if _passes(r))
    failing = len(scored) - passing
    errored = asked - len(scored)
    records += [_unmeasured({"passed": False, "errored": True, "score": None, "graders": []})
                ] * max(0, asked - len(records))
    base = {"passed": passing, "failed": failing, "errored": errored, "runs": records}

    if runs is not None and passing >= pass_at:
        return {**base, "verdict": "pass", "reason": None}
    if runs is not None and failing > n - pass_at:
        return {**base, "verdict": "fail", "reason": None}
    if killed:
        reason = TIMEOUT
    elif runs is None:
        reason = RUNNER_ERROR
    elif isinstance(doc, dict) and doc.get("partial") and doc.get("partialReason") == "cost_ceiling":
        reason = COST_CAP
    elif isinstance(doc, dict) and doc.get("partial") and doc.get("partialReason") == "auth_failed":
        reason = RUNNER_ERROR
    else:
        reason = RUNS_ERRORED
    return {**base, "verdict": "not-run", "reason": reason}


def not_run(case: str, n: int, reason: str) -> dict:
    return {"case": case, "verdict": "not-run", "reason": reason,
            "passed": 0, "failed": 0, "errored": n, "runs": []}


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def execute(runner, cwd: Path, timeout: float) -> tuple[int | None, bool, bool]:
    """Run the runner in its own session. Returns ``(exit_code, killed, missing)``.

    The process group is killed and reaped on timeout and on any other exit from the wait,
    an exception or interrupt included."""
    try:
        proc = subprocess.Popen(
            runner.argv,
            env=runner.env,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return None, False, True
    done = False
    try:
        try:
            code = proc.wait(timeout=timeout)
            done = True
            return code, False, False
        except subprocess.TimeoutExpired:
            return None, True, False
    finally:
        if not done:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()


def _requester(env: dict[str, str], harnesses: list | None):
    if harnesses is None:
        from trailhead.harness import detect_harnesses

        harnesses = detect_harnesses(env)

    def request(**kw):
        return harnesses[0].eval_runner(env=env, **kw) if harnesses else None

    return request


def _first_batch(n: int, pass_at: int) -> int:
    return max(1, min(pass_at, n - pass_at + 1, n))


def _valid_cost(doc: object) -> float | None:
    cost = doc.get("costUsd") if isinstance(doc, dict) else None
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        return None
    return float(cost) if math.isfinite(cost) and cost >= 0 else None


def _canary(work_dir: Path, request, override, timeout: float, env: dict[str, str],
            execute_fn, pin, strict_tmp: bool) -> dict:
    run_json = work_dir / canary.RUN_JSON
    run_json.unlink(missing_ok=True)
    with _call_tmpdir(env, strict_tmp) as canary_tmp:
        runner = pin(request(
            tmpdir=canary_tmp,
            plugin_dir=canary.plugin_dir(),
            case=canary.CASE,
            runs=1,
            model=canary.MODEL,
            allow_tools=list(canary.ALLOW_TOOLS),
            max_cost_usd=canary.MAX_COST_USD,
            json_path=run_json,
            output_dir=work_dir / canary.RUN_OUTPUT,
            eval_dir=canary.EVAL_DIR,
        ), override)
        if runner is None:
            return not_run(canary.CASE, 1, NO_RUNNER)
        code, killed, missing = execute_fn(runner, work_dir, timeout)
        if missing:
            return not_run(canary.CASE, 1, RUNNER_MISSING)
        return {"case": canary.CASE, **_decide(read_json(run_json), 1, 1, code, killed)}


def run_canary(*, work_dir: Path, timeout_seconds: float, execute, resolve_runner, pin,
               env: dict[str, str], harnesses: list | None = None,
               strict_tmp: bool = False) -> dict:
    """Run the sandbox-write canary once (one run, on a cheap pinned model) and return its result.
    Files go under ``work_dir``; nothing is cached."""
    override = resolve_runner(env)
    if override is False:
        return not_run(canary.CASE, 1, RUNNER_MISSING)
    return _canary(Path(work_dir), _requester(env, harnesses), override,
                   min(canary.TIMEOUT_SECONDS, timeout_seconds), env, execute, pin, strict_tmp)


def decide_case(*, plugin_dir: Path, case: str, runs: int, pass_at: int, runner: dict,
                output_dir: Path, timeout_seconds: float, execute, resolve_runner, pin,
                env: dict[str, str], harnesses: list | None = None, strict_tmp: bool = False,
                work_dir: Path | None = None, clock=time.monotonic, canary=None,
                label: str = "eval") -> dict:
    """Run one case in batches and return its decided result (shape in the module docstring).

    ``runner`` is the plan's runner block (``model``, ``allow_tools``, ``max_cost_usd``).
    ``work_dir`` (default ``output_dir``) is where runners run and the canary's files go.
    ``canary(run, timeout)``, when given, supplies the canary result: it may return a cached
    one, or call ``run(timeout)`` to run the canary; by default the canary runs every time.
    Raises ``TmpPathTooLong`` and ``TmpBaseRefused`` before any runner starts."""
    override = resolve_runner(env)
    if override is False:
        print(f"{label}: {RUNNER_OVERRIDE_ENV} must be an absolute path to an "
              f"executable file that only you or root can write", file=sys.stderr)
        return not_run(case, runs, RUNNER_MISSING)

    output_dir = Path(output_dir)
    work_dir = output_dir if work_dir is None else Path(work_dir)
    output_dir.mkdir(mode=0o700, exist_ok=True)
    json_path = output_dir / f"{case}.json"
    json_path.unlink(missing_ok=True)
    request = _requester(env, harnesses)

    with _call_tmpdir(env, strict_tmp) as case_tmp:
        return _run_planned(plugin_dir, case, runs, pass_at, runner, output_dir, json_path,
                            timeout_seconds, request, override, env, case_tmp, execute, pin,
                            strict_tmp, work_dir, clock, canary)


def _run_planned(plugin_dir, case, n, pass_at, runner_cfg, runs_dir, json_path, timeout_seconds,
                 request, override, env, case_tmp, execute_fn, pin, strict_tmp, work_dir, clock,
                 canary_hook) -> dict:
    cap = runner_cfg["max_cost_usd"]

    def runner_for(batch: int, max_cost: float, json_out: Path, output_dir: Path):
        return pin(request(
            tmpdir=case_tmp,
            plugin_dir=plugin_dir,
            case=case,
            runs=batch,
            model=runner_cfg["model"],
            allow_tools=list(runner_cfg["allow_tools"]),
            max_cost_usd=max_cost,
            json_path=json_out,
            output_dir=output_dir,
        ), override)

    batch = _first_batch(n, pass_at)
    runner = runner_for(batch, cap, json_path, runs_dir / case)
    if runner is None:
        return not_run(case, n, NO_RUNNER)

    started = clock()

    def run_the_canary(timeout: float) -> dict:
        return _canary(work_dir, request, override, timeout, env, execute_fn, pin, strict_tmp)

    canary_timeout = min(canary.TIMEOUT_SECONDS, timeout_seconds)
    gate = (canary_hook(run_the_canary, canary_timeout) if canary_hook
            else run_the_canary(canary_timeout))
    if gate["verdict"] != "pass":
        reason = SANDBOX_UNWRITABLE if gate["verdict"] == "fail" else gate["reason"]
        return not_run(case, n, reason)

    merged: list[dict] = []
    asked, spent, first_elapsed = 0, 0.0, 0.0
    while True:
        call_start = clock()
        code, killed, missing = execute_fn(
            runner, work_dir, max(1.0, timeout_seconds - (call_start - started)))
        if missing:
            return not_run(case, n, RUNNER_MISSING)
        if not asked:
            first_elapsed = clock() - call_start
        doc = read_json(json_path)
        runs = _runs_of(doc)
        segment = (runs or [])[:batch]
        segment += [{"error": "missing"}] * (batch - len(segment))
        merged += segment
        view = {**(doc if isinstance(doc, dict) else {}), "cases": [{"arms": {"with": merged}}]}
        decided = _decide(view, n, pass_at, code, killed, asked=asked + batch, killed_from=asked)
        asked += batch
        if runs is None and decided["verdict"] == "not-run":
            decided["reason"] = TIMEOUT if killed else RUNNER_ERROR
        result = {"case": case, **decided}
        if decided["verdict"] != "not-run" or decided["reason"] != RUNS_ERRORED:
            return result
        if all(_is_errored(r) for r in segment) or asked >= n:
            return result

        cost = _valid_cost(doc)
        spent = cap if cost is None else spent + cost
        left = round(cap - spent, 6)
        if left <= 0:
            return {**result, "reason": COST_CAP}
        time_left = timeout_seconds - (clock() - started)
        if time_left < first_elapsed:
            return {**result, "reason": TIMEOUT}

        need = min(pass_at - decided["passed"], n - pass_at + 1 - decided["failed"])
        batch = max(1, min(need, n - asked))
        json_path = runs_dir / case / f"batch-{asked}.json"
        json_path.parent.mkdir(mode=0o700, exist_ok=True)
        json_path.unlink(missing_ok=True)
        runner = runner_for(batch, left, json_path, runs_dir / case / f"batch-{asked}")
