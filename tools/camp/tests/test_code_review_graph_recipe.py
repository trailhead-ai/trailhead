"""Recipe-shape regression test for the code-review-graph provision task.

The real `code-review-graph` CLI is never invoked here — that verification stays
a manual end-to-end run (see tools/camp/scripts/e2e_code_review_graph.sh). This
test pins the *shape* of the recipe so it can't silently regress:

1. The shipped example config resolves the trailhead member's
   `code-review-graph` task to a SINGLE optional provision-phase `build` step —
   `code-review-graph build --repo {worktree}` — with no rsync/seed step. (A
   seed+incremental-`update` recipe was proven broken: incremental `update`
   never repaths untouched nodes, so a seeded graph keeps pointing at the
   original repo root. A full `build` is correct by construction.)
2. Run through the pure task runner, a single-step optional provision task is
   marked "ok" only on exit 0 of that one step, and a non-zero exit marks it
   "failed" WITHOUT raising (the task is not required).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_GROUPS_EXAMPLE_DIR = _REPO_ROOT / "tools" / "camp" / "groups.example"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.provision.tasks import (  # noqa: E402
    TaskResult,
    run_member_tasks,
)

_PY = sys.executable

_TASK_NAME = "code-review-graph"


def _resolved_recipe() -> dict:
    """The trailhead member's resolved code-review-graph task from the shipped
    example config."""
    from camp.group.config import load_group

    cfg = load_group(_GROUPS_EXAMPLE_DIR / "trailhead.toml")
    member = next(m for m in cfg["members"] if m["name"] == "trailhead")
    return next(t for t in member["tasks"] if t["name"] == _TASK_NAME)


# ---------------------------------------------------------------------------
# 1. The example config recipe is a single provision-phase `build` step.
# ---------------------------------------------------------------------------


def test_example_config_recipe_is_single_build_step() -> None:
    task = _resolved_recipe()

    assert task["phase"] == "provision"
    assert task["required"] is False
    assert isinstance(task["timeout_seconds"], int) and task["timeout_seconds"] > 0
    assert task["steps"] == [
        {
            "name": "build",
            "cmd": ["code-review-graph", "build", "--repo", "{worktree}"],
        }
    ]


def test_example_config_recipe_timeout_is_at_least_900_seconds() -> None:
    """A full build of this worktree measured 914s on an otherwise-idle box —
    5x the prior 180s budget. 900s leaves ~2x headroom under provisioning
    contention; a regression back toward 180 must fail this test."""
    task = _resolved_recipe()

    assert task["timeout_seconds"] >= 900


def test_example_config_recipe_has_no_seed_step() -> None:
    """Guard against a seed/rsync step creeping back into the recipe."""
    task = _resolved_recipe()

    flat = " ".join(tok for step in task["steps"] for tok in step["cmd"])
    assert "rsync" not in flat
    assert "update" not in flat
    assert len(task["steps"]) == 1


# ---------------------------------------------------------------------------
# 2. Through the runner: ok only on exit 0; non-zero fails without raising.
# ---------------------------------------------------------------------------


def _recipe_shaped_task(stub_cmd: list[str]) -> dict:
    """A task mirroring the code-review-graph recipe shape (single optional
    provision step) but with the real `build` argv swapped for a stub — the
    runner executes argv directly, so the real CLI stays out of the suite."""
    return {
        "name": _TASK_NAME,
        "phase": "provision",
        "required": False,
        "timeout_seconds": 180,
        "steps": [stub_cmd],
    }


def _context(worktree: Path) -> dict[str, str]:
    return {
        "repo_root": "/repos/upstream",
        "worktree": str(worktree),
        "workspace": "my-workspace",
        "slug": "my-slug",
    }


def test_recipe_task_marked_ok_on_exit_zero(tmp_path: Path) -> None:
    task = _recipe_shaped_task([_PY, "-c", "import sys; sys.exit(0)"])

    results = run_member_tasks(
        [task], "provision", _context(tmp_path), completed={}
    )

    assert results == [TaskResult(name=_TASK_NAME, state="ok")]


def test_recipe_task_marked_failed_on_nonzero_without_raising(tmp_path: Path) -> None:
    stub = [_PY, "-c", "import sys; sys.stderr.write('build failed'); sys.exit(1)"]
    task = _recipe_shaped_task(stub)

    # required is False, so a failing step must NOT raise — it records a failed
    # TaskResult and returns.
    results = run_member_tasks(
        [task], "provision", _context(tmp_path), completed={}
    )

    assert results[0].name == _TASK_NAME
    assert results[0].state == "failed"
    assert results[0].failing_step == stub
