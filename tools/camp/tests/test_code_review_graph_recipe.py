"""Recipe-shape regression test for the code-review-graph activate task.

The real `code-review-graph` CLI is never invoked here — that verification stays
a manual end-to-end run (see tools/camp/scripts/e2e_code_review_graph.sh). This
test pins the *shape* of the recipe so it can't silently regress:

1. The shipped example config resolves the trailhead member's
   `code-review-graph` task to a SINGLE optional activate-phase `build` step —
   `code-review-graph build --repo {worktree}` — with no rsync/seed step. (A
   seed+incremental-`update` recipe was proven broken: incremental `update`
   never repaths untouched nodes, so a seeded graph keeps pointing at the
   original repo root. A full `build` is correct by construction.) It runs in
   the activate phase, not provision, because a full build measures ~914s and
   the provision phase must stay cheap and boot-facing.
2. Run through the pure task runner, a single-step optional activate task is
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
# 1. The example config recipe is a single activate-phase `build` step.
# ---------------------------------------------------------------------------


def test_example_config_recipe_is_single_build_step() -> None:
    task = _resolved_recipe()

    assert task["phase"] == "activate"
    assert task["required"] is False
    assert isinstance(task["timeout_seconds"], int) and task["timeout_seconds"] > 0
    assert task["steps"] == [
        {
            "name": "build",
            "cmd": ["code-review-graph", "build", "--repo", "{worktree}"],
        }
    ]


def test_example_config_recipe_declares_a_capability_consequence() -> None:
    """A member waiting on this activate-phase task must be told what it can't
    do yet — the graph MCP server has no graph until this task settles."""
    task = _resolved_recipe()

    assert isinstance(task["capability"], str) and task["capability"].strip()
    assert "graph" in task["capability"].lower()


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
    activate step) but with the real `build` argv swapped for a stub — the
    runner executes argv directly, so the real CLI stays out of the suite."""
    return {
        "name": _TASK_NAME,
        "phase": "activate",
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
        [task], "activate", _context(tmp_path), completed={}
    )

    assert results == [TaskResult(name=_TASK_NAME, state="ok")]


def test_recipe_task_marked_failed_on_nonzero_without_raising(tmp_path: Path) -> None:
    stub = [_PY, "-c", "import sys; sys.stderr.write('build failed'); sys.exit(1)"]
    task = _recipe_shaped_task(stub)

    # required is False, so a failing step must NOT raise — it records a failed
    # TaskResult and returns.
    results = run_member_tasks(
        [task], "activate", _context(tmp_path), completed={}
    )

    assert results[0].name == _TASK_NAME
    assert results[0].state == "failed"
    assert results[0].failing_step == stub


# ---------------------------------------------------------------------------
# 3. The two config homes agree: the repo example and the chezmoi source
#    template must declare the same phase + timeout for this task, or the
#    known two-homes drift silently reappears in this very change.
# ---------------------------------------------------------------------------

_CHEZMOI_TRAILHEAD_TMPL = (
    Path.home() / ".local" / "share" / "chezmoi" / "private_dot_config" / "camp"
    / "groups" / "trailhead.toml.tmpl"
)


def _resolved_recipe_from_chezmoi_template() -> dict:
    """The trailhead member's code-review-graph task as declared in the
    chezmoi source template.

    The full .tmpl file is not valid TOML — it carries go-template
    conditionals (`{{ if eq .chezmoi.os "darwin" }}`) around the per-machine
    member list and the [release] merge_order. The `[tasks.*]` blocks
    compared here sit between those two templated regions and carry no
    template syntax of their own, so the untemplated slice between them is
    valid TOML on its own and parses to the same task definition every
    machine's rendered config would produce — verified below by asserting
    the slice is free of template delimiters before parsing it, rather than
    assuming it.
    """
    import tomllib

    assert _CHEZMOI_TRAILHEAD_TMPL.is_file(), (
        f"chezmoi template not found at {_CHEZMOI_TRAILHEAD_TMPL} — the two-homes "
        "agreement check cannot run without it; this must fail, not skip."
    )
    text = _CHEZMOI_TRAILHEAD_TMPL.read_text(encoding="utf-8")

    start = text.index("[tasks.code-review-graph]")
    end = text.index("[release]")
    fragment = text[start:end]
    assert "{{" not in fragment and "}}" not in fragment, (
        "the [tasks.*] slice of the chezmoi template now contains template "
        "syntax — the untemplated-slice assumption this comparison relies on "
        "no longer holds"
    )

    parsed = tomllib.loads(fragment)
    return parsed["tasks"][_TASK_NAME]


def test_example_config_and_chezmoi_template_agree_on_phase_and_timeout() -> None:
    example_task = _resolved_recipe()
    chezmoi_task = _resolved_recipe_from_chezmoi_template()

    assert example_task["phase"] == chezmoi_task.get("phase", "provision")
    assert example_task["timeout_seconds"] == chezmoi_task["timeout_seconds"]


# ---------------------------------------------------------------------------
# 4. End-to-end: a workspace created from the example config runs mcp-config
#    at creation and does NOT run the reassigned code-review-graph task until
#    activation. This is the headline assertion for the whole plan — a
#    workspace creation that used to pay ~914s now pays only mcp-config's
#    sub-second copy.
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"], check=True, capture_output=True
    )
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init", "--no-gpg-sign"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", str(path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "fetch", "origin", "--quiet"], check=True, capture_output=True
    )


def test_workspace_creation_runs_mcp_config_but_not_reassigned_tasks_until_activation(
    tmp_path: Path,
) -> None:
    import copy

    from camp.group.config import load_group
    from camp.group.manifest import read_central_manifest
    from camp.group.resolve import central_state_dir
    from camp.provision.activation import run_activate_tasks_in_background
    from camp.provision.reconcile import reconcile_worktree

    cfg = load_group(_GROUPS_EXAMPLE_DIR / "trailhead.toml")
    trailhead_member = copy.deepcopy(
        next(m for m in cfg["members"] if m["name"] == "trailhead")
    )

    repo = tmp_path / "trailhead"
    _init_git_repo(repo)
    trailhead_member["repo_root"] = str(repo)
    trailhead_member["base"] = "origin/main"

    group = {
        "group": {"name": "e2ereassigng"},
        "members": [trailhead_member],
        "branch_pattern": "worktree-{slug}",
    }
    env = {"CAMP_STATE_DIR": str(tmp_path / "camp-state")}

    # Creation: only the provision-phase mcp-config task runs.
    reconcile_worktree(group, "s", env=env)

    mpath = central_state_dir("e2ereassigng", env=env) / "worktrees" / "s" / "manifest.json"
    entry = read_central_manifest(mpath)["members"][0]
    assert entry["tasks"]["mcp-config"]["state"] == "ok"
    assert _TASK_NAME not in entry["tasks"], (
        "code-review-graph must not run at workspace creation now that it is "
        "an activate-phase task"
    )

    # Activation: the reassigned task now runs.
    run_activate_tasks_in_background(group, "s", "trailhead", env=env)

    entry = read_central_manifest(mpath)["members"][0]
    assert _TASK_NAME in entry["tasks"], (
        "code-review-graph must run once the member is activated"
    )
