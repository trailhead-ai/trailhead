"""Run one planned eval case through the harness's eval runner and record a verdict.

``run_case(state, case)`` reads ``<state>/plan.json`` (shape in ``select``), asks the
first detected harness for its eval runner, runs it bounded by a wall-clock timeout
(default 540 s, so the call returns under the 600 s tool ceiling), and writes
``<state>/results/<case>.json``::

    {"case": "<name>",
     "verdict": "pass" | "fail" | "not-run",
     "passed": int, "failed": int, "errored": int,     # scored passes / scored fails / errored runs
     "reason": null | "harness-has-no-eval-runner" | "runner-missing" | "timeout"
                    | "cost-cap" | "runner-error" | "runs-errored" | "runner-sandbox-unwritable"
                    | "case-removed-on-branch" | "corpus-new-on-branch" | "head-moved",
     "runs": [{"passed": bool, "errored": bool,
               "graders": [{"name": str, "passed": bool}, ...],
               "duration_seconds": number | null, "cost_usd": number | null,
               "judge_cost_usd": number | null}, ...]}

``reason`` is null on pass and fail. The only runner values a run record carries are grader
names and booleans plus the run's duration, cost and judge cost; each of those three is the
runner's own number only when it is finite, non-negative and within a sanity bound (3600 seconds,
100 dollars), else null, and null for a run the runner never returned or that was killed. A stored
pass or result written without them reads as null. Never grader explanations. The runner's own result stays
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

Before the canary and the runner, ``run`` looks the case up in the pass cache (``cache.py``, which
holds the key and the store). A hit records ``verdict`` ``pass`` with ``cached`` true and the stored
counts and runs, and starts no runner and no canary. A fresh ``pass`` is stored after the case ran,
only when the fingerprint recomputed after the run equals the one looked up before it; a fail and a
not-run never are. A case whose inputs hold a symlink has no key (never looked up, never stored), and
with ``PORTAGE_EVAL_RUNNER`` set the cache is neither read nor written. ``cached`` is false on every
result of a case that ran.

A case is decided in batches, not one ``--runs n`` call. The first call asks for
``min(k, n - k + 1)`` runs (2 for 3 of 2), the fewest that could decide it. After each call
the rule above is applied to every run so far (only the runs asked for are counted). While
neither verdict is reached, the next call asks for the smallest batch that could still decide
it, ``min(k - passing, n - k + 1 - failing)`` runs, capped at the runs not yet asked, until
``n`` have been asked. ``errored`` counts errored runs among those asked, so a case decided
early does not count the runs it never asked for as errored. ``runs`` holds every call's run
records in order. Each call's runner result stays at ``<state>/runs/<case>.json`` (the first)
and ``<state>/runs/<case>/batch-<runs asked before it>.json`` (later calls).

A later call is started only when all of these hold; otherwise the case is not-run with the
reason named, and the runs so far stay in ``runs``:

- the last call added at least one scored run (``runs-errored`` otherwise);
- the case's ``max_cost_usd`` minus the ``costUsd`` reported so far is above zero; a missing,
  non-numeric or negative ``costUsd`` counts as the whole cap spent (``cost-cap``). That
  remainder is passed to the call as its ``--max-cost-usd``;
- the wall-clock budget left is at least the first call's elapsed time (``timeout``). The
  budget left is the call's timeout minus the time since ``run`` started, the canary included,
  and it is the timeout the later call runs under. ``clock`` (default ``time.monotonic``)
  supplies the time.

The sandbox-write canary (``canary.py``) runs once per state directory before the first
case, not once per batch, and is cached at ``<state>/canary.json`` — the same shape as a case result, with
``case`` ``"sandbox-write"`` and a single run (runs 1, pass_at 1, same deciding)::

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

Each runner call, the canary and the case, gets a private temp directory as ``TMPDIR``,
created by ``run`` under ``$XDG_CACHE_HOME/pegt`` (``$HOME/.cache/pegt`` when
``XDG_CACHE_HOME`` is unset or not absolute, both read from the gate's own environment) and
removed when the call ends. The runner's sandbox could not write when its temp dir was under
``/tmp`` or when the path was longer than 51 characters (runner 2.1.308, the tested Linux
host; an empirical limit of that runner). A per-call path over 51 characters is recorded
not-run ``runner-sandbox-unwritable`` before any runner starts, the canary included, with a
stderr line naming the length. The canary still catches a runner that cannot write. A base
that is a symlink, owned by another user or group- or world-writable makes the case not-run
``runner-error`` (a stderr line names the problem) and no runner starts. The directory is placed
through the harness's ``eval_runner`` hook (``tmpdir``).

A result describes the head the plan was made at. Before it looks anything up or starts anything, ``run``
asks git in the plan's ``repo``: unless ``HEAD`` is the plan's ``head`` and no tracked file differs
from it (untracked files do not count), the case is recorded as not-run ``head-moved`` (a plan with
no ``head``, or a repo git cannot answer for, is the same). Plan again at the current head.

A plan entry with ``new_runner_grants`` true (its corpus has no mapping at the base, so its
runner grants are unreviewed) is recorded as not-run ``corpus-new-on-branch`` the same way: no
harness lookup, no canary, no runner.

A plan entry with ``case_removed`` true (the branch removes the case) is recorded as
not-run ``case-removed-on-branch`` before any harness lookup or canary; no runner starts.
A runner that cannot be launched (``OSError``: not found, not executable, bad format) is
not-run ``runner-missing``.

An unexpected exception during ``run`` is recorded by ``record_crash`` as not-run
``runner-error`` before the command exits 1.

Refusals (``RunRefused``, exit 2, no runner started): a state directory that is a
symlink, not owned by the caller or not mode 0700; a case that is not in the plan or
whose name fails the mapping's shape; a plugin root that leaves the repo.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

from portage.eval_core import run as core
from portage.eval_core.run import (
    RUNNER_ERROR,
    RUNNER_OVERRIDE_ENV,
    SANDBOX_UNWRITABLE,
    TmpBaseRefused,
    TmpPathTooLong,
    not_run,
)

from . import cache as pass_cache
from . import canary
from .mapping import CASE_NAME

DEFAULT_TIMEOUT_SECONDS = 540
MAX_TIMEOUT_SECONDS = 540

CASE_REMOVED = "case-removed-on-branch"
CORPUS_NEW = "corpus-new-on-branch"
HEAD_MOVED = "head-moved"


class RunRefused(Exception):
    pass


def check_state(state: Path) -> None:
    """Refuse a state directory that is not a real, caller-owned directory with mode 0700.

    Every reader of a state directory (``run``, ``verdict``, the merge backstop) goes
    through this, because a state others can write is a verdict others can forge."""
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


def _at_planned_head(plan: dict) -> bool:
    """Whether ``plan["repo"]`` is at ``plan["head"]`` with no tracked file modified. Any failure
    to ask git is a no."""
    head, repo = plan.get("head"), plan.get("repo")
    if not isinstance(head, str) or not isinstance(repo, str):
        return False
    try:
        now = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True)
        dirty = subprocess.run(["git", "-C", repo, "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True)
    except OSError:
        return False
    return (now.returncode == 0 and dirty.returncode == 0
            and now.stdout.strip() == head and not dirty.stdout.strip())


def atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _write_json(path: Path, doc: dict) -> None:
    atomic_write(path, json.dumps(doc, indent=2) + "\n")


def _write_result(state: Path, case: str, result: dict) -> None:
    results = state / "results"
    results.mkdir(mode=0o700, exist_ok=True)
    _write_json(results / f"{case}.json", result)


_execute = core.execute


def run_case(
    state: Path,
    case: str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    harnesses: list | None = None,
    env: dict[str, str] | None = None,
    clock=time.monotonic,
) -> dict:
    """Validate, run and record one case; return the result written."""
    state = Path(state)
    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise RunRefused(f"timeout must be between 1 and {MAX_TIMEOUT_SECONDS} seconds")
    check_state(state)
    plan, entry = _load_case(state, case)
    plugin_dir = _plugin_dir(plan, entry)
    n, pass_at = entry["runs"], entry["pass_at"]
    if not _at_planned_head(plan):
        return _record(state, case, not_run(case, n, HEAD_MOVED))
    if entry.get("case_removed") is True:
        return _record(state, case, not_run(case, n, CASE_REMOVED))
    if entry.get("new_runner_grants") is True:
        return _record(state, case, not_run(case, n, CORPUS_NEW))
    runner_cfg = entry["runner"]

    if env is None:
        env = dict(os.environ)
    key = None if RUNNER_OVERRIDE_ENV in env else _cache_key(plugin_dir, entry)
    hit = pass_cache.lookup(key, env) if key else None
    if hit is not None:
        return _record(state, case, {"case": case, **hit, "reason": None, "cached": True})
    if harnesses is None:
        from trailhead.harness import detect_harnesses

        harnesses = detect_harnesses(env)

    try:
        result = _run_planned(state, case, n, pass_at, plugin_dir, runner_cfg, timeout_seconds,
                              harnesses, env, clock)
        if key and _cache_key(plugin_dir, entry) == key:
            pass_cache.store(key, result, env)
        return result
    except TmpPathTooLong as e:
        print(f"eval-gate run: {e}", file=sys.stderr)
        return _record(state, case, not_run(case, n, SANDBOX_UNWRITABLE))
    except TmpBaseRefused as e:
        print(f"eval-gate run: {e}", file=sys.stderr)
        return _record(state, case, not_run(case, n, RUNNER_ERROR))


def _cache_key(plugin_dir: Path, entry: dict) -> str | None:
    try:
        return pass_cache.fingerprint(plugin_dir, entry)
    except (OSError, KeyError, TypeError):
        return None


def _run_planned(state, case, n, pass_at, plugin_dir, runner_cfg, timeout_seconds, harnesses,
                 env, clock) -> dict:
    def cached_canary(run_canary, timeout):
        cached = canary.read_cached(state)
        if cached is not None:
            return cached
        fresh = _ordered(run_canary(timeout))
        _write_json(state / canary.CACHE_NAME, fresh)
        return fresh

    result = core.decide_case(
        plugin_dir=plugin_dir, case=case, runs=n, pass_at=pass_at, runner=runner_cfg,
        output_dir=state / "runs", timeout_seconds=timeout_seconds, work_dir=state,
        execute=_execute, resolve_runner=_runner_override, pin=_pin,
        env=env, harnesses=harnesses, clock=clock, canary=cached_canary, label="eval-gate run")
    return _record(state, case, result)


def record_crash(state: Path, case: str) -> None:
    """Best effort: record not-run ``runner-error`` for ``case`` after an unexpected failure,
    so the verdict never reads a crash as a missing result. Does nothing when the state
    directory or the case would have been refused."""
    try:
        state = Path(state)
        check_state(state)
        _, entry = _load_case(state, case)
        _record(state, case, not_run(case, entry["runs"], RUNNER_ERROR))
    except Exception:
        pass


def _ordered(result: dict) -> dict:
    ordered = {k: result[k] for k in
               ("case", "verdict", "passed", "failed", "errored", "reason", "runs")}
    ordered["runs"] = [
        {**{k: v for k, v in r.items() if k != "score"},
         **{field: r.get(field) for field, _, _ in core.TIMING_FIELDS}}
        for r in ordered["runs"]]
    return ordered


def _record(state: Path, case: str, result: dict) -> dict:
    ordered = {**_ordered(result), "cached": result.get("cached", False)}
    _write_result(state, case, ordered)
    return ordered


def _trusted_uids() -> set[int]:
    return {0, os.geteuid()}


def _runner_override(env: dict[str, str]) -> str | None | bool:
    """The validated ``PORTAGE_EVAL_RUNNER`` path, ``None`` when unset, ``False`` when unusable."""
    value = env.get(RUNNER_OVERRIDE_ENV)
    if value is None:
        return None
    if not (os.path.isabs(value) and os.path.isfile(value) and os.access(value, os.X_OK)):
        return False
    st = os.stat(value)
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH) or st.st_uid not in _trusted_uids():
        return False
    return value


def _pin(runner, override: str | None):
    if runner is None or override is None:
        return runner
    return type(runner)(argv=[override, *runner.argv[1:]], env=runner.env)
