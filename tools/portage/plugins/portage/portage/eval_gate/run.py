"""Run one planned eval case through the harness's eval runner and record a verdict.

``run_case(state, case)`` reads ``<state>/plan.json`` (shape in ``select``), asks the
first detected harness for its eval runner, runs it bounded by a wall-clock timeout
(default 540 s, so the call returns under the 600 s tool ceiling), and writes
``<state>/results/<case>.json``::

    {"case": "<name>",
     "verdict": "pass" | "fail" | "not-run",
     "passed": int, "failed": int, "errored": int,     # scored passes / scored fails / errored runs
     "reason": null | "harness-has-no-eval-runner" | "runner-missing" | "timeout"
                    | "cost-cap" | "runner-error" | "runs-errored" | "runner-sandbox-unwritable",
     "runs": [{"passed": bool, "errored": bool,
               "graders": [{"name": str, "passed": bool}, ...]}, ...]}

``reason`` is null on pass and fail. ``runs`` is built only from grader names and
booleans — never runner output or grader explanations. The runner's own result stays
at ``<state>/runs/<case>.json`` (artifacts in ``<state>/runs/<case>/``).

Deciding from the runner's JSON, never its exit code (a scored fail, a scaffold
failure, a timeout and a network error all exit 1): the run list is
``cases[0].arms.with``. A run is errored when ``error`` is set or
``skippedPaidGraders`` is true; runs requested but absent count as errored. A scored
run passes at score 1.0. Over ``n`` runs and ``pass_at`` k: pass when passing >= k;
fail when scored failing > n - k; otherwise nothing is decidable and the reason is
``timeout`` (the runner was killed), ``cost-cap`` (partial, ``cost_ceiling``),
``runner-error`` (``auth_failed`` partial, or a non-zero exit with no usable JSON),
else ``runs-errored``.

The sandbox-write canary (``canary.py``) runs once per state directory before the first
case and is cached at ``<state>/canary.json`` — the same shape as a case result, with
``case`` ``"sandbox-write"`` and a single run (runs 1, pass_at 1, same ``_decide``)::

    {"case": "sandbox-write", "verdict": "pass" | "fail" | "not-run", "passed": int,
     "failed": int, "errored": int, "reason": null | <a reason code>, "runs": [...]}

A cached result, whatever its verdict, is reused by every later ``run`` in that state
directory. Canary ``pass``: the case runs. Canary ``fail``: the case is not-run with
reason ``runner-sandbox-unwritable`` (the runner's sandboxed Bash cannot write). Canary
``not-run``: the case is not-run with the canary's own reason. Either way the case
runner is never started. The canary is bounded by 120 s inside the call's total bound;
the case gets what remains.

``PORTAGE_EVAL_RUNNER`` (read from the gate's own environment) pins the runner binary
for this machine, for both the canary and the case: an absolute path to an executable
regular file, else the case is not-run ``runner-missing`` (a stderr line names the
variable) and nothing starts. It replaces the harness's own ``claude`` lookup and is
never forwarded into the child's environment.

Refusals (``RunRefused``, exit 2, no runner started): a state directory that is a
symlink, not owned by the caller or not mode 0700; a case that is not in the plan or
whose name fails the mapping's shape; a plugin root that leaves the repo.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

from . import canary
from .mapping import CASE_NAME

DEFAULT_TIMEOUT_SECONDS = 540
MAX_TIMEOUT_SECONDS = 540

NO_RUNNER = "harness-has-no-eval-runner"
RUNNER_MISSING = "runner-missing"
TIMEOUT = "timeout"
COST_CAP = "cost-cap"
RUNNER_ERROR = "runner-error"
RUNS_ERRORED = "runs-errored"
SANDBOX_UNWRITABLE = "runner-sandbox-unwritable"
RUNNER_OVERRIDE_ENV = "PORTAGE_EVAL_RUNNER"


class RunRefused(Exception):
    pass


def _check_state(state: Path) -> None:
    try:
        st = os.lstat(state)
    except OSError as e:
        raise RunRefused(f"state directory unusable: {e}") from e
    if stat.S_ISLNK(st.st_mode):
        raise RunRefused(f"state directory {state} is a symlink")
    if not stat.S_ISDIR(st.st_mode):
        raise RunRefused(f"state directory {state} is not a directory")
    if st.st_uid != os.geteuid():
        raise RunRefused(f"state directory {state} is owned by another user")
    if st.st_mode & 0o777 != 0o700:
        raise RunRefused(f"state directory {state} must be mode 0700")


def _load_case(state: Path, case: str) -> tuple[dict, dict]:
    if not isinstance(case, str) or not CASE_NAME.match(case):
        raise RunRefused(f"case name {case!r} must match {CASE_NAME.pattern}")
    try:
        plan = json.loads((state / "plan.json").read_text())
        entries = [c for c in plan["cases"] if c["case"] == case]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise RunRefused(f"plan.json unreadable: {e}") from e
    if not entries:
        raise RunRefused(f"case {case!r} is not in the plan")
    return plan, entries[0]


def _plugin_dir(plan: dict, entry: dict) -> Path:
    root = PurePosixPath(entry["plugin_root"])
    if root.is_absolute() or ".." in root.parts:
        raise RunRefused(f"plugin root {entry['plugin_root']!r} leaves the repo")
    return Path(plan["repo"]) / root


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


def _run_record(run: dict) -> dict:
    graders = run.get("graders") if isinstance(run.get("graders"), list) else []
    return {
        "passed": not _is_errored(run) and _passes(run),
        "errored": _is_errored(run),
        "graders": [
            {"name": g["name"], "passed": bool(g.get("passed"))}
            for g in graders
            if isinstance(g, dict) and isinstance(g.get("name"), str)
        ],
    }


def _decide(doc: object, n: int, pass_at: int, exit_code: int | None, killed: bool) -> dict:
    runs = _runs_of(doc)
    records = [_run_record(r) for r in (runs or [])][:n]
    scored = [r for r in (runs or [])[:n] if not _is_errored(r)]
    passing = sum(1 for r in scored if _passes(r))
    failing = len(scored) - passing
    errored = n - len(scored)
    records += [{"passed": False, "errored": True, "graders": []}] * max(0, n - len(records))
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


def _write_result(state: Path, case: str, result: dict) -> None:
    results = state / "results"
    results.mkdir(mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=results, prefix=f".{case}.", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")
    os.replace(tmp, results / f"{case}.json")


def _execute(runner, state: Path, timeout: float) -> tuple[int | None, bool, bool]:
    """Run the runner in its own session. Returns ``(exit_code, killed, missing)``."""
    try:
        proc = subprocess.Popen(
            runner.argv,
            env=runner.env,
            cwd=state,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return None, False, True
    try:
        return proc.wait(timeout=timeout), False, False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        return None, True, False


def run_case(
    state: Path,
    case: str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    harnesses: list | None = None,
    env: dict[str, str] | None = None,
) -> dict:
    """Validate, run and record one case; return the result written."""
    state = Path(state)
    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise RunRefused(f"timeout must be between 1 and {MAX_TIMEOUT_SECONDS} seconds")
    _check_state(state)
    plan, entry = _load_case(state, case)
    plugin_dir = _plugin_dir(plan, entry)
    n, pass_at = entry["runs"], entry["pass_at"]
    runner_cfg = entry["runner"]

    if env is None:
        env = dict(os.environ)
    if harnesses is None:
        from trailhead.harness import detect_harnesses

        harnesses = detect_harnesses(env)

    override = _runner_override(env)
    if override is False:
        print(f"eval-gate run: {RUNNER_OVERRIDE_ENV} must be an absolute path to an "
              f"executable file", file=sys.stderr)
        return _record(state, case, _not_run(case, n, RUNNER_MISSING))

    runs_dir = state / "runs"
    runs_dir.mkdir(mode=0o700, exist_ok=True)
    json_path = runs_dir / f"{case}.json"
    json_path.unlink(missing_ok=True)

    def request(**kw):
        return harnesses[0].eval_runner(env=env, **kw) if harnesses else None

    runner = _pin(request(
        plugin_dir=plugin_dir,
        case=case,
        runs=n,
        model=runner_cfg["model"],
        allow_tools=list(runner_cfg["allow_tools"]),
        max_cost_usd=runner_cfg["max_cost_usd"],
        json_path=json_path,
        output_dir=runs_dir / case,
    ), override)
    if runner is None:
        return _record(state, case, _not_run(case, n, NO_RUNNER))

    started = time.monotonic()
    gate = canary.read_cached(state)
    if gate is None:
        gate = _run_canary(state, request, override, min(canary.TIMEOUT_SECONDS, timeout_seconds))
        canary.write_cached(state, gate)
    if gate["verdict"] != "pass":
        reason = SANDBOX_UNWRITABLE if gate["verdict"] == "fail" else gate["reason"]
        return _record(state, case, _not_run(case, n, reason))

    remaining = max(1.0, timeout_seconds - (time.monotonic() - started))
    code, killed, missing = _execute(runner, state, remaining)
    if missing:
        return _record(state, case, _not_run(case, n, RUNNER_MISSING))
    return _record(state, case, {"case": case, **_decide(_read_json(json_path), n, pass_at, code, killed)})


def _not_run(case: str, n: int, reason: str) -> dict:
    return {"case": case, "verdict": "not-run", "reason": reason,
            "passed": 0, "failed": 0, "errored": n, "runs": []}


def _record(state: Path, case: str, result: dict) -> dict:
    ordered = {k: result[k] for k in
               ("case", "verdict", "passed", "failed", "errored", "reason", "runs")}
    _write_result(state, case, ordered)
    return ordered


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _runner_override(env: dict[str, str]) -> str | None | bool:
    """The validated ``PORTAGE_EVAL_RUNNER`` path, ``None`` when unset, ``False`` when unusable."""
    value = env.get(RUNNER_OVERRIDE_ENV)
    if value is None:
        return None
    if os.path.isabs(value) and os.path.isfile(value) and os.access(value, os.X_OK):
        return value
    return False


def _pin(runner, override: str | None):
    if runner is None or override is None:
        return runner
    return type(runner)(argv=[override, *runner.argv[1:]], env=runner.env)


def _run_canary(state: Path, request, override: str | None, timeout: float) -> dict:
    run_json = state / canary.RUN_JSON
    run_json.unlink(missing_ok=True)
    runner = _pin(request(
        plugin_dir=canary.plugin_dir(),
        case=canary.CASE,
        runs=1,
        model=canary.MODEL,
        allow_tools=list(canary.ALLOW_TOOLS),
        max_cost_usd=canary.MAX_COST_USD,
        json_path=run_json,
        output_dir=state / canary.RUN_OUTPUT,
        eval_dir=canary.EVAL_DIR,
    ), override)
    if runner is None:
        result = _not_run(canary.CASE, 1, NO_RUNNER)
    else:
        code, killed, missing = _execute(runner, state, timeout)
        if missing:
            result = _not_run(canary.CASE, 1, RUNNER_MISSING)
        else:
            result = {"case": canary.CASE, **_decide(_read_json(run_json), 1, 1, code, killed)}
    return {k: result[k] for k in
            ("case", "verdict", "passed", "failed", "errored", "reason", "runs")}
