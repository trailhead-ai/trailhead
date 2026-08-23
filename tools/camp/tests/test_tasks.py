"""Tests for provision/tasks.py — the pure config-driven task runner.

Test contract (see the module docstring for the task/context/completed shapes):

1. Placeholder substitution: single- and multi-placeholder argv tokens resolve
   correctly (e.g. "{repo_root}/foo/{slug}" combines two placeholders in one
   token).
2. A task already completed "ok" (per `completed`) is skipped entirely; a
   previously "failed" task IS re-run.
3. A step failure short-circuits the REMAINING steps of that task, but later
   tasks in the list still run.
4. A hung step fails via a real subprocess timeout (no mocking — the timeout
   mechanism itself is exercised) rather than hanging forever.
5. A required task's failure raises TaskError, naming member/task/step, but
   only after recording the TaskResult (carried on the exception).
6. An optional (non-required) task's failure returns a failed TaskResult
   without raising.
7. The persisted stderr excerpt is capped/truncated at a bounded length.
8. Phase filtering selects only the tasks matching the requested phase.
9. Retry cleanup: a task's declared `cleanup` argv runs before its steps only
   when the prior recorded state is "failed", under the identical execution
   contract as a step (argv list, shell=False, cwd confined to the worktree,
   its own timeout); a cleanup failure or timeout aborts the retry and is
   distinguishable from a step failure.
10. Per-task budget: an optional wall-clock deadline for the whole task
    yields a distinct "over-budget" state when exceeded, independent of the
    per-step timeout_seconds.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.provision.tasks import (  # noqa: E402
    STDERR_EXCERPT_MAX_CHARS,
    TaskError,
    TaskResult,
    run_member_tasks,
    substitute_step,
)

_PY = sys.executable


def _touch_step(marker: Path) -> list[str]:
    """An argv step that creates `marker` on success."""
    return [_PY, "-c", f"open({str(marker)!r}, 'w').close()"]


def _exit_step(code: int, stderr: str = "") -> list[str]:
    return [_PY, "-c", f"import sys; sys.stderr.write({stderr!r}); sys.exit({code})"]


def _sleep_step(seconds: float) -> list[str]:
    return [_PY, "-c", f"import time; time.sleep({seconds})"]


def _missing_binary_step() -> list[str]:
    return ["this-binary-does-not-exist-anywhere-on-path"]


def _context(worktree: Path, **extra: str) -> dict[str, str]:
    ctx = {
        "repo_root": "/repos/upstream",
        "worktree": str(worktree),
        "workspace": "my-workspace",
        "slug": "my-slug",
    }
    ctx.update(extra)
    return ctx


# ---------------------------------------------------------------------------
# 1. Placeholder substitution
# ---------------------------------------------------------------------------


def test_substitute_step_single_placeholder():
    result = substitute_step(["{repo_root}"], {"repo_root": "/repos/upstream"})
    assert result == ["/repos/upstream"]


def test_substitute_step_multi_placeholder_in_one_token():
    result = substitute_step(
        ["{repo_root}/foo/{slug}"],
        {"repo_root": "/repos/upstream", "slug": "my-slug"},
    )
    assert result == ["/repos/upstream/foo/my-slug"]


def test_substitute_step_multiple_tokens_and_plain_tokens():
    result = substitute_step(
        ["cmd", "--repo", "{worktree}", "--tag", "{workspace}-{slug}"],
        {"worktree": "/wt", "workspace": "ws", "slug": "sl"},
    )
    assert result == ["cmd", "--repo", "/wt", "--tag", "ws-sl"]


# ---------------------------------------------------------------------------
# 2. completed-ok skip / failed re-run
# ---------------------------------------------------------------------------


def test_task_already_ok_is_skipped(tmp_path: Path):
    marker = tmp_path / "marker"
    tasks = [
        {
            "name": "seed",
            "phase": "provision",
            "steps": [_touch_step(marker)],
        }
    ]
    results = run_member_tasks(
        tasks, "provision", _context(tmp_path), completed={"seed": "ok"}
    )
    assert not marker.exists()
    assert results == [TaskResult(name="seed", state="skipped")]


def test_previously_failed_task_is_rerun(tmp_path: Path):
    marker = tmp_path / "marker"
    tasks = [
        {
            "name": "seed",
            "phase": "provision",
            "steps": [_touch_step(marker)],
        }
    ]
    results = run_member_tasks(
        tasks, "provision", _context(tmp_path), completed={"seed": "failed"}
    )
    assert marker.exists()
    assert results == [TaskResult(name="seed", state="ok")]


# ---------------------------------------------------------------------------
# 3. step failure short-circuits the task, later tasks still run
# ---------------------------------------------------------------------------


def test_step_failure_short_circuits_remaining_steps_of_same_task(tmp_path: Path):
    later_marker = tmp_path / "later_marker"
    tasks = [
        {
            "name": "broken",
            "phase": "provision",
            "steps": [_exit_step(1, "boom"), _touch_step(later_marker)],
        }
    ]
    results = run_member_tasks(tasks, "provision", _context(tmp_path), completed={})
    assert not later_marker.exists()
    assert len(results) == 1
    assert results[0].name == "broken"
    assert results[0].state == "failed"
    assert results[0].failing_step == _exit_step(1, "boom")


def test_later_tasks_still_run_after_earlier_task_fails(tmp_path: Path):
    later_marker = tmp_path / "later_marker"
    tasks = [
        {"name": "broken", "phase": "provision", "steps": [_exit_step(1, "boom")]},
        {"name": "fine", "phase": "provision", "steps": [_touch_step(later_marker)]},
    ]
    results = run_member_tasks(tasks, "provision", _context(tmp_path), completed={})
    assert later_marker.exists()
    assert [r.name for r in results] == ["broken", "fine"]
    assert results[0].state == "failed"
    assert results[1].state == "ok"


# ---------------------------------------------------------------------------
# 4. hung step fails via real subprocess timeout
# ---------------------------------------------------------------------------


def test_hung_step_fails_via_timeout_not_hang(tmp_path: Path):
    tasks = [
        {
            "name": "hangs",
            "phase": "provision",
            "timeout_seconds": 1,
            "steps": [_sleep_step(10)],
        }
    ]
    start = time.monotonic()
    results = run_member_tasks(tasks, "provision", _context(tmp_path), completed={})
    elapsed = time.monotonic() - start
    assert elapsed < 5, "step should have been killed by the 1s timeout, not run for 10s"
    assert results[0].state == "failed"
    assert results[0].failing_step is not None


# ---------------------------------------------------------------------------
# 5. required task failure raises TaskError (after recording the result)
# ---------------------------------------------------------------------------


def test_required_task_failure_raises_task_error_naming_member_task_step(tmp_path: Path):
    tasks = [
        {
            "name": "seed",
            "phase": "provision",
            "required": True,
            "steps": [_exit_step(1, "boom")],
        }
    ]
    ctx = _context(tmp_path, member="trailhead")

    with pytest.raises(TaskError) as exc_info:
        run_member_tasks(tasks, "provision", ctx, completed={})

    message = str(exc_info.value)
    assert "trailhead" in message
    assert "seed" in message
    assert exc_info.value.results == [
        TaskResult(
            name="seed",
            state="failed",
            failing_step=_exit_step(1, "boom"),
            stderr_excerpt="boom",
        )
    ]


def test_required_task_failure_does_not_skip_recording_result(tmp_path: Path):
    tasks = [
        {"name": "a", "phase": "provision", "required": True, "steps": [_exit_step(1)]},
        {"name": "b", "phase": "provision", "steps": [_exit_step(1)]},
    ]
    with pytest.raises(TaskError) as exc_info:
        run_member_tasks(tasks, "provision", _context(tmp_path), completed={})

    # Task "a" is required and fails first — its result is recorded, then the
    # run raises. Task "b" never runs.
    assert [r.name for r in exc_info.value.results] == ["a"]


# ---------------------------------------------------------------------------
# 6. optional task failure returns a failed TaskResult, no raise
# ---------------------------------------------------------------------------


def test_optional_task_failure_returns_failed_result_without_raising(tmp_path: Path):
    tasks = [
        {
            "name": "seed",
            "phase": "provision",
            "required": False,
            "steps": [_exit_step(1, "boom")],
        }
    ]
    results = run_member_tasks(tasks, "provision", _context(tmp_path), completed={})
    assert results == [
        TaskResult(
            name="seed",
            state="failed",
            failing_step=_exit_step(1, "boom"),
            stderr_excerpt="boom",
        )
    ]


def test_optional_task_missing_binary_returns_failed_result_without_raising(
    tmp_path: Path,
):
    tasks = [
        {
            "name": "seed",
            "phase": "provision",
            "required": False,
            "steps": [_missing_binary_step()],
        }
    ]
    results = run_member_tasks(tasks, "provision", _context(tmp_path), completed={})
    assert len(results) == 1
    assert results[0].name == "seed"
    assert results[0].state == "failed"
    assert results[0].failing_step == _missing_binary_step()
    assert "this-binary-does-not-exist-anywhere-on-path" in results[0].stderr_excerpt


def test_required_task_missing_binary_raises_task_error(tmp_path: Path):
    tasks = [
        {
            "name": "seed",
            "phase": "provision",
            "required": True,
            "steps": [_missing_binary_step()],
        }
    ]
    ctx = _context(tmp_path, member="trailhead")

    with pytest.raises(TaskError) as exc_info:
        run_member_tasks(tasks, "provision", ctx, completed={})

    message = str(exc_info.value)
    assert "trailhead" in message
    assert "seed" in message
    assert len(exc_info.value.results) == 1
    result = exc_info.value.results[0]
    assert result.state == "failed"
    assert result.failing_step == _missing_binary_step()
    assert "this-binary-does-not-exist-anywhere-on-path" in result.stderr_excerpt


# ---------------------------------------------------------------------------
# 7. stderr excerpt is capped
# ---------------------------------------------------------------------------


def test_stderr_excerpt_is_capped(tmp_path: Path):
    huge = "x" * (STDERR_EXCERPT_MAX_CHARS * 3)
    tasks = [
        {
            "name": "noisy",
            "phase": "provision",
            "steps": [_exit_step(1, huge)],
        }
    ]
    results = run_member_tasks(tasks, "provision", _context(tmp_path), completed={})
    assert len(results[0].stderr_excerpt) <= STDERR_EXCERPT_MAX_CHARS + len("…(truncated)")
    assert len(results[0].stderr_excerpt) < len(huge)


# ---------------------------------------------------------------------------
# 8. phase filtering
# ---------------------------------------------------------------------------


def test_phase_filtering_selects_only_matching_phase(tmp_path: Path):
    provision_marker = tmp_path / "provision_marker"
    activate_marker = tmp_path / "activate_marker"
    tasks = [
        {"name": "seed", "phase": "provision", "steps": [_touch_step(provision_marker)]},
        {"name": "hook", "phase": "activate", "steps": [_touch_step(activate_marker)]},
    ]
    results = run_member_tasks(tasks, "provision", _context(tmp_path), completed={})
    assert provision_marker.exists()
    assert not activate_marker.exists()
    assert [r.name for r in results] == ["seed"]


# ---------------------------------------------------------------------------
# 9. retry cleanup
# ---------------------------------------------------------------------------


def test_cleanup_runs_before_retry_of_failed_task_then_steps_run(tmp_path: Path):
    cleanup_marker = tmp_path / "cleanup_marker"
    step_marker = tmp_path / "step_marker"
    tasks = [
        {
            "name": "seed",
            "phase": "provision",
            "cleanup": _touch_step(cleanup_marker),
            "steps": [_touch_step(step_marker)],
        }
    ]
    results = run_member_tasks(
        tasks, "provision", _context(tmp_path), completed={"seed": "failed"}
    )
    assert cleanup_marker.exists()
    assert step_marker.exists()
    assert results == [TaskResult(name="seed", state="ok")]


def test_cleanup_does_not_run_on_first_run(tmp_path: Path):
    cleanup_marker = tmp_path / "cleanup_marker"
    step_marker = tmp_path / "step_marker"
    tasks = [
        {
            "name": "seed",
            "phase": "provision",
            "cleanup": _touch_step(cleanup_marker),
            "steps": [_touch_step(step_marker)],
        }
    ]
    results = run_member_tasks(tasks, "provision", _context(tmp_path), completed={})
    assert not cleanup_marker.exists()
    assert step_marker.exists()
    assert results == [TaskResult(name="seed", state="ok")]


def test_cleanup_does_not_run_when_prior_state_is_ok(tmp_path: Path):
    cleanup_marker = tmp_path / "cleanup_marker"
    tasks = [
        {
            "name": "seed",
            "phase": "provision",
            "cleanup": _touch_step(cleanup_marker),
            "steps": [_touch_step(tmp_path / "step_marker")],
        }
    ]
    results = run_member_tasks(
        tasks, "provision", _context(tmp_path), completed={"seed": "ok"}
    )
    assert not cleanup_marker.exists()
    assert results == [TaskResult(name="seed", state="skipped")]


def test_cleanup_subprocess_call_shape_is_argv_shell_false_cwd_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    captured: dict[str, Any] = {}
    real_run = subprocess.run

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv == ["cleanup-cmd"]:
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return real_run(argv, **kwargs)

    monkeypatch.setattr("camp.provision.tasks.subprocess.run", fake_run)

    tasks = [
        {
            "name": "seed",
            "phase": "provision",
            "cleanup": ["cleanup-cmd"],
            "steps": [_touch_step(tmp_path / "step_marker")],
        }
    ]
    run_member_tasks(
        tasks, "provision", _context(tmp_path), completed={"seed": "failed"}
    )

    assert captured["argv"] == ["cleanup-cmd"]
    assert isinstance(captured["argv"], list)
    assert captured["kwargs"].get("shell", False) is False
    assert captured["kwargs"]["cwd"] == str(tmp_path)


def test_cleanup_failure_aborts_retry_and_is_distinguishable_from_step_failure(
    tmp_path: Path,
):
    step_marker = tmp_path / "step_marker"
    tasks = [
        {
            "name": "seed",
            "phase": "provision",
            "cleanup": _exit_step(1, "cleanup boom"),
            "steps": [_touch_step(step_marker)],
        }
    ]
    results = run_member_tasks(
        tasks, "provision", _context(tmp_path), completed={"seed": "failed"}
    )
    assert not step_marker.exists()
    assert results == [
        TaskResult(
            name="seed",
            state="failed",
            failing_step=_exit_step(1, "cleanup boom"),
            stderr_excerpt="cleanup boom",
            cleanup_failed=True,
        )
    ]


def test_cleanup_timeout_aborts_retry_without_hanging(tmp_path: Path):
    tasks = [
        {
            "name": "seed",
            "phase": "provision",
            "timeout_seconds": 1,
            "cleanup": _sleep_step(10),
            "steps": [_touch_step(tmp_path / "step_marker")],
        }
    ]
    start = time.monotonic()
    results = run_member_tasks(
        tasks, "provision", _context(tmp_path), completed={"seed": "failed"}
    )
    elapsed = time.monotonic() - start
    assert elapsed < 5, "cleanup should have been killed by the 1s timeout, not run for 10s"
    assert results[0].state == "failed"
    assert results[0].cleanup_failed is True


def test_required_task_cleanup_failure_raises_task_error(tmp_path: Path):
    tasks = [
        {
            "name": "seed",
            "phase": "provision",
            "required": True,
            "cleanup": _exit_step(1, "cleanup boom"),
            "steps": [_touch_step(tmp_path / "step_marker")],
        }
    ]
    with pytest.raises(TaskError) as exc_info:
        run_member_tasks(
            tasks, "provision", _context(tmp_path), completed={"seed": "failed"}
        )
    assert exc_info.value.results == [
        TaskResult(
            name="seed",
            state="failed",
            failing_step=_exit_step(1, "cleanup boom"),
            stderr_excerpt="cleanup boom",
            cleanup_failed=True,
        )
    ]


def test_npm_ci_shape_cleanup_removes_leftover_tree_then_retry_succeeds(tmp_path: Path):
    leftover = tmp_path / "node_modules"
    leftover.mkdir()
    (leftover / "wreckage").write_text("partial")

    check_step = [
        _PY,
        "-c",
        f"import sys, os; sys.exit(1 if os.path.exists({str(leftover)!r}) else 0)",
    ]
    tasks = [
        {
            "name": "npm-ci",
            "phase": "provision",
            "cleanup": ["rm", "-rf", str(leftover)],
            "steps": [check_step],
        }
    ]
    results = run_member_tasks(
        tasks, "provision", _context(tmp_path), completed={"npm-ci": "failed"}
    )
    assert not leftover.exists()
    assert results == [TaskResult(name="npm-ci", state="ok")]


# ---------------------------------------------------------------------------
# 10. per-task budget / over-budget outcome
# ---------------------------------------------------------------------------


def test_task_exceeding_deadline_yields_over_budget_not_failed(tmp_path: Path):
    tasks = [
        {
            "name": "slow",
            "phase": "provision",
            "steps": [_sleep_step(0.5)],
        }
    ]
    results = run_member_tasks(
        tasks,
        "provision",
        _context(tmp_path),
        completed={},
        task_deadline_seconds=0.05,
    )
    assert results == [TaskResult(name="slow", state="over-budget")]


def test_step_timeout_still_yields_failed_even_with_budget_room(tmp_path: Path):
    tasks = [
        {
            "name": "hangs",
            "phase": "provision",
            "timeout_seconds": 1,
            "steps": [_sleep_step(10)],
        }
    ]
    results = run_member_tasks(
        tasks,
        "provision",
        _context(tmp_path),
        completed={},
        task_deadline_seconds=100,
    )
    assert results[0].state == "failed"


def test_task_within_deadline_yields_ok(tmp_path: Path):
    marker = tmp_path / "marker"
    tasks = [
        {
            "name": "seed",
            "phase": "provision",
            "steps": [_touch_step(marker)],
        }
    ]
    results = run_member_tasks(
        tasks,
        "provision",
        _context(tmp_path),
        completed={},
        task_deadline_seconds=30,
    )
    assert results == [TaskResult(name="seed", state="ok")]


def test_over_budget_does_not_raise_for_required_task_and_later_tasks_still_run(
    tmp_path: Path,
):
    later_marker = tmp_path / "later_marker"
    tasks = [
        {
            "name": "slow",
            "phase": "provision",
            "required": True,
            "steps": [_sleep_step(0.5)],
        },
        {"name": "fine", "phase": "provision", "steps": [_touch_step(later_marker)]},
    ]
    results = run_member_tasks(
        tasks,
        "provision",
        _context(tmp_path),
        completed={},
        task_deadline_seconds=0.05,
    )
    assert later_marker.exists()
    assert [r.name for r in results] == ["slow", "fine"]
    assert results[0].state == "over-budget"
    assert results[1].state == "ok"


def test_task_deadline_absent_never_produces_over_budget(tmp_path: Path):
    marker = tmp_path / "marker"
    tasks = [
        {
            "name": "seed",
            "phase": "provision",
            "steps": [_sleep_step(0.2), _touch_step(marker)],
        }
    ]
    results = run_member_tasks(tasks, "provision", _context(tmp_path), completed={})
    assert results == [TaskResult(name="seed", state="ok")]
