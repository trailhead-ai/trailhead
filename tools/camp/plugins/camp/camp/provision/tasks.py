"""Pure, injectable config-driven task runner.

run_member_tasks(tasks, phase, context, completed) -> list[TaskResult]

This module is standalone: it accepts plain data and has no dependency on
`camp.group.config` (or any manifest reader/writer). `camp.provision.reconcile`
and `camp.provision.activation` are its consumers — they read a member's
normalized `tasks` list from config, adapt each step's shape to what this
runner expects (see `tasks` below), call `run_member_tasks`, persist the
returned `TaskResult`s into the central manifest, and print warnings on
optional-task failure. This module itself does none of that wiring.

Expected shapes (documented here because config.py's step shape — a
`{"name", "cmd"}` dict, for legible per-step reporting — doesn't match what
this runner takes; each caller adapts locally rather than either module
reaching into the other's shape):

    tasks: list of task definitions, each a mapping with:
        name:            str — task name (used in results + error messages).
        steps:           list of argv steps; each step is a list[str] token
                         list, run shell=False (no shell string). Steps run
                         IN ORDER; the first failing/timed-out step fails the
                         task and skips the rest of that task's steps.
        phase:           "provision" | "activate" (default "provision").
        required:        bool (default False). A required task's failure
                         raises TaskError; an optional task's failure is
                         recorded and run_member_tasks keeps going.
        timeout_seconds: int | None — per-step subprocess timeout. Falls back
                         to DEFAULT_STEP_TIMEOUT_SECONDS when absent/falsy —
                         this module owns that default, not the config layer.

    context: a mapping used both for {placeholder} substitution in argv
        tokens AND for building legible error messages. At minimum it should
        carry repo_root / worktree / workspace / slug (the placeholders a
        config-driven task recipe is expected to reference); "worktree" is
        also used as the subprocess cwd. An optional "member" key — not
        necessarily a placeholder target — is read for TaskError's message;
        when absent, the message falls back to "slug". All values are
        coerced to str before substitution, so a Path is accepted directly.

    completed: a mapping of task name -> prior state string (e.g. the
        manifest's persisted per-task state). Only a prior state of "ok"
        skips a task (run-once-on-success semantics); any other state
        (missing, "failed", etc.) re-runs it.

Substitution: each argv token is passed through `str.format(**context)`,
so a single token may combine multiple placeholders, e.g.
"{repo_root}/foo/{slug}". This mirrors the `_substitute` pattern already
used for harness-profile placeholders (camp/launch/profile.py) — no parallel
implementation.

Execution: shell=False, cwd=worktree, output captured (text mode). A
TimeoutExpired is treated exactly like a failing step (it fails the task
just the same). An OSError raised by subprocess.run itself — most notably
FileNotFoundError when a step's argv[0] names a binary missing from PATH —
is likewise treated as a failing step rather than allowed to escape
run_member_tasks, so the `required` flag still governs the outcome.

Persisted output: `TaskResult.stderr_excerpt` is capped at
STDERR_EXCERPT_MAX_CHARS — these excerpts land in a durable, re-displayed
manifest, so they must never carry unbounded step output.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Per-step subprocess timeout applied when a task doesn't set its own
# timeout_seconds. Mirrors the FETCH_TIMEOUT_SECONDS precedent in
# reconcile.py: an unresponsive step must fail its task rather than hang
# every future run for the workspace.
DEFAULT_STEP_TIMEOUT_SECONDS = 300

# Persisted stderr excerpts land in the durable manifest and are re-displayed
# by `camp status` — cap them so one chatty failing step can't bloat the
# manifest or the terminal.
STDERR_EXCERPT_MAX_CHARS = 2000

_TRUNCATION_SUFFIX = "…(truncated)"


class TaskError(Exception):
    """Raised when a required task fails.

    Carries `results`: the full list of TaskResult objects accumulated up to
    and including the failed required task's own result, so the caller can
    persist them before the exception unwinds the run.
    """

    def __init__(self, message: str, results: list["TaskResult"]):
        super().__init__(message)
        self.results = results


@dataclass(frozen=True)
class TaskResult:
    """Outcome of running (or skipping) one task.

    state: "ok" | "failed" | "skipped".
    failing_step: the substituted argv of the first failing/timed-out step,
        or None if the task didn't fail.
    stderr_excerpt: capped stderr (or stdout, if stderr was empty) from the
        failing step; "" if the task didn't fail.
    """

    name: str
    state: str
    failing_step: list[str] | None = None
    stderr_excerpt: str = ""


def substitute_step(step: list[str], context: Mapping[str, Any]) -> list[str]:
    """Substitute {placeholder} tokens in each argv token of `step`.

    A single token may combine multiple placeholders (e.g.
    "{repo_root}/foo/{slug}") — str.format resolves all of them in one pass.
    `context` values are coerced to str before substitution.
    """
    str_context = {key: str(value) for key, value in context.items()}
    return [token.format(**str_context) for token in step]


def _cap_excerpt(text: str) -> str:
    text = text.strip()
    if len(text) <= STDERR_EXCERPT_MAX_CHARS:
        return text
    return text[:STDERR_EXCERPT_MAX_CHARS] + _TRUNCATION_SUFFIX


def run_member_tasks(
    tasks: list[Mapping[str, Any]],
    phase: str,
    context: Mapping[str, Any],
    completed: Mapping[str, str],
) -> list[TaskResult]:
    """Run a member's tasks matching `phase`, in order.

    Skips a task whose prior `completed` state is "ok" (run-once-on-success).
    A step failure or timeout fails its task and skips that task's remaining
    steps, but later tasks in `tasks` still run. A required task's failure
    raises TaskError AFTER its TaskResult has been appended to the returned
    (and exception-carried) results; an optional task's failure is recorded
    and the run continues.
    """
    results: list[TaskResult] = []
    member = str(context.get("member", context.get("slug", "<unknown>")))
    worktree = str(context["worktree"])

    for task in tasks:
        if task.get("phase", "provision") != phase:
            continue

        name = task["name"]
        if completed.get(name) == "ok":
            results.append(TaskResult(name=name, state="skipped"))
            continue

        timeout = task.get("timeout_seconds") or DEFAULT_STEP_TIMEOUT_SECONDS
        required = bool(task.get("required", False))

        failing_step: list[str] | None = None
        stderr_excerpt = ""

        for step in task.get("steps", []):
            argv = substitute_step(step, context)
            try:
                proc = subprocess.run(
                    argv,
                    cwd=worktree,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                failing_step = argv
                stderr_excerpt = _cap_excerpt(f"step timed out after {timeout}s: {argv}")
                break
            except OSError as exc:
                failing_step = argv
                stderr_excerpt = _cap_excerpt(f"step failed to start: {argv}: {exc}")
                break

            if proc.returncode != 0:
                failing_step = argv
                stderr_excerpt = _cap_excerpt(proc.stderr or proc.stdout)
                break

        if failing_step is None:
            results.append(TaskResult(name=name, state="ok"))
            continue

        result = TaskResult(
            name=name,
            state="failed",
            failing_step=failing_step,
            stderr_excerpt=stderr_excerpt,
        )
        results.append(result)

        if required:
            raise TaskError(
                f"camp: required task {name!r} failed for member {member!r} "
                f"at step {failing_step}: {stderr_excerpt}",
                results,
            )

    return results
