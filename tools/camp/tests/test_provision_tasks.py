"""Tests for config-driven task wiring into the provision path.

Both provision entry points — the async per-member path (provision_member,
driven by cmd_setup_group) and the synchronous reconcile path
(reconcile_worktree phase 2) — run a member's provision-phase tasks through
the shared task runner instead of the retired single bootstrap command:

- A REQUIRED task failure fails the member exactly as a bootstrap failure did:
  on the setup path the member flips to failed + reason; on the reconcile path
  reconcile_worktree raises ReconcileError and writes no manifest.
- An OPTIONAL task failure records the failed state in the manifest, prints a
  one-line stderr warning (member + task + `camp status`), and continues.
- Per-task completion is persisted in the member's manifest `tasks` map and is
  run-once-on-success: a task recorded "ok" is skipped on the next run; a task
  recorded "failed" (or absent) re-runs.

Fixtures use real synthetic git repos in tmp_path + CAMP_STATE_DIR injection.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
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
    # Self-origin so the configured base `origin/main` resolves locally.
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", str(path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "fetch", "origin", "--quiet"], check=True, capture_output=True
    )


def _camp_state_env(tmp_path: Path) -> dict[str, str]:
    state_root = tmp_path / "camp-state"
    state_root.mkdir(parents=True, exist_ok=True)
    return {"CAMP_STATE_DIR": str(state_root)}


def _provision_task(
    name: str,
    cmd: list[str],
    *,
    required: bool = False,
    phase: str = "provision",
) -> dict:
    """Build a member task in the config-resolved shape (steps carry {name, cmd})."""
    return {
        "name": name,
        "phase": phase,
        "required": required,
        "timeout_seconds": None,
        "steps": [{"name": name, "cmd": cmd}],
    }


def _make_group(name: str, members: list[dict]) -> dict:
    return {"group": {"name": name}, "members": members, "branch_pattern": "worktree-{slug}"}


def _manifest_path(group_name: str, slug: str, env: dict[str, str]) -> Path:
    from camp.group.resolve import central_state_dir

    return central_state_dir(group_name, env=env) / "worktrees" / slug / "manifest.json"


# ---------------------------------------------------------------------------
# provision_member / cmd_setup_group path
# ---------------------------------------------------------------------------


def test_optional_task_failure_member_ready_recorded_and_warned(tmp_path, capsys):
    """An optional task failure leaves the member ready, records the task failed
    in the manifest, and prints a one-line stderr warning."""
    from camp.provision.provision import seed_pending_workspace
    from camp.provision.lifecycle import cmd_setup_group
    from camp.group.manifest import read_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "optg",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [_provision_task("flaky", ["false"], required=False)],
            }
        ],
    )

    seed_pending_workspace(group, "s", env=env)
    result = cmd_setup_group(group, "s", env=env)

    assert result["members"]["repo"]["provision_state"] == "ready"

    data = read_central_manifest(_manifest_path("optg", "s", env))
    entry = data["members"][0]
    assert entry["tasks"]["flaky"]["state"] == "failed"

    err = capsys.readouterr().err
    assert "flaky" in err
    assert "repo" in err
    assert "camp status" in err


def test_required_task_failure_member_failed_with_task_in_reason(tmp_path):
    """A required task failure flips the member to failed with the task name in
    the reason, and records the failed state in the manifest."""
    from camp.provision.provision import seed_pending_workspace
    from camp.provision.lifecycle import cmd_setup_group
    from camp.group.manifest import read_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "reqg",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [_provision_task("migrate", ["false"], required=True)],
            }
        ],
    )

    seed_pending_workspace(group, "s", env=env)
    result = cmd_setup_group(group, "s", env=env)

    member_result = result["members"]["repo"]
    assert member_result["provision_state"] == "failed"
    assert "migrate" in member_result["reason"]

    data = read_central_manifest(_manifest_path("reqg", "s", env))
    entry = data["members"][0]
    assert entry["provision_state"] == "failed"
    assert entry["tasks"]["migrate"]["state"] == "failed"


def test_setup_retry_skips_ok_task_reruns_failed_required(tmp_path):
    """On the setup path a member kept 'failed' by a required task is re-provisioned,
    but a task already recorded ok is skipped while the failing task re-runs."""
    from camp.provision.provision import seed_pending_workspace
    from camp.provision.lifecycle import cmd_setup_group
    from camp.group.manifest import read_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    seed_runs = tmp_path / "seed_runs"
    migrate_runs = tmp_path / "migrate_runs"
    group = _make_group(
        "retryg",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [
                    _provision_task("seed", ["sh", "-c", f"echo x >> {seed_runs}"]),
                    _provision_task(
                        "migrate",
                        ["sh", "-c", f"echo x >> {migrate_runs}; false"],
                        required=True,
                    ),
                ],
            }
        ],
    )

    seed_pending_workspace(group, "s", env=env)
    # First setup: seed succeeds (recorded ok), migrate fails → member failed.
    r1 = cmd_setup_group(group, "s", env=env)
    assert r1["members"]["repo"]["provision_state"] == "failed"
    # Second setup: member is failed → re-provisioned. seed is skipped (ok),
    # migrate re-runs (still failing).
    r2 = cmd_setup_group(group, "s", env=env)
    assert r2["members"]["repo"]["provision_state"] == "failed"

    assert seed_runs.read_text().count("x") == 1
    assert migrate_runs.read_text().count("x") == 2

    entry = read_central_manifest(_manifest_path("retryg", "s", env))["members"][0]
    assert entry["tasks"]["seed"]["state"] == "ok"
    assert entry["tasks"]["migrate"]["state"] == "failed"


# ---------------------------------------------------------------------------
# reconcile_worktree path
# ---------------------------------------------------------------------------


def test_reconcile_raises_reconcile_error_on_required_task_failure(tmp_path):
    """reconcile_worktree raises ReconcileError on a required task failure and
    writes no manifest (bootstrap-failure atomicity)."""
    from camp.provision.reconcile import reconcile_worktree, ReconcileError

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "rcg",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [_provision_task("migrate", ["false"], required=True)],
            }
        ],
    )

    with pytest.raises(ReconcileError) as exc_info:
        reconcile_worktree(group, "s", env=env)

    assert "migrate" in str(exc_info.value)
    assert not _manifest_path("rcg", "s", env).exists()


def test_reconcile_optional_failure_warns_and_writes_manifest(tmp_path, capsys):
    """An optional task failure on the reconcile path warns on stderr, records
    the failed state, and still writes the manifest."""
    from camp.provision.reconcile import reconcile_worktree
    from camp.group.manifest import read_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "rcoptg",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [_provision_task("flaky", ["false"], required=False)],
            }
        ],
    )

    reconcile_worktree(group, "s", env=env)

    err = capsys.readouterr().err
    assert "flaky" in err
    assert "repo" in err
    assert "camp status" in err

    data = read_central_manifest(_manifest_path("rcoptg", "s", env))
    assert data["members"][0]["tasks"]["flaky"]["state"] == "failed"


def test_second_reconcile_skips_ok_reruns_failed(tmp_path):
    """A second reconcile skips a task recorded ok and re-runs one recorded failed."""
    from camp.provision.reconcile import reconcile_worktree
    from camp.group.manifest import read_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    ok_runs = tmp_path / "ok_runs"
    flaky_runs = tmp_path / "flaky_runs"
    group = _make_group(
        "rerung",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [
                    _provision_task("ok", ["sh", "-c", f"echo x >> {ok_runs}"]),
                    _provision_task(
                        "flaky", ["sh", "-c", f"echo x >> {flaky_runs}; false"], required=False
                    ),
                ],
            }
        ],
    )

    reconcile_worktree(group, "s", env=env)
    reconcile_worktree(group, "s", env=env)

    # The ok task ran once (skipped on the second run); the failed task re-ran.
    assert ok_runs.read_text().count("x") == 1
    assert flaky_runs.read_text().count("x") == 2

    data = read_central_manifest(_manifest_path("rerung", "s", env))
    tasks = data["members"][0]["tasks"]
    assert tasks["ok"]["state"] == "ok"
    assert tasks["flaky"]["state"] == "failed"


def test_task_states_persist_and_survive_reread(tmp_path):
    """Persisted task states survive repeated reads of the manifest."""
    from camp.provision.reconcile import reconcile_worktree
    from camp.group.manifest import read_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "persistg",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [_provision_task("ok", ["true"])],
            }
        ],
    )

    reconcile_worktree(group, "s", env=env)

    mpath = _manifest_path("persistg", "s", env)
    first = read_central_manifest(mpath)["members"][0]["tasks"]
    second = read_central_manifest(mpath)["members"][0]["tasks"]
    assert first == {"ok": {"state": "ok"}}
    assert second == first


# ---------------------------------------------------------------------------
# manifest persistence primitive
# ---------------------------------------------------------------------------


def test_flip_persists_tasks_without_dropping_other_states(tmp_path):
    """flip_member_state_unlocked merges the given task states into the member's
    existing `tasks` map rather than replacing it (preserves other-phase states)."""
    from camp.group.manifest import (
        flip_member_state_unlocked,
        read_central_manifest,
        reconcile_lock,
        write_central_manifest,
    )

    mpath = tmp_path / "manifest.json"
    write_central_manifest(
        mpath,
        {
            "schema_version": 1,
            "group": "g",
            "slug": "s",
            "branch": "worktree-s",
            "members": [
                {
                    "name": "repo",
                    "repo_root": "/tmp/repo",
                    "worktree_path": str(tmp_path / "repo"),
                    "provision_state": "pending",
                    "tasks": {"dep-install": {"state": "ok"}},
                }
            ],
        },
    )

    with reconcile_lock(mpath.parent):
        flip_member_state_unlocked(
            mpath, "repo", "ready", tasks={"bootstrap": {"state": "ok"}}
        )

    entry = read_central_manifest(mpath)["members"][0]
    assert entry["provision_state"] == "ready"
    assert entry["tasks"] == {
        "dep-install": {"state": "ok"},
        "bootstrap": {"state": "ok"},
    }


# ---------------------------------------------------------------------------
# grep-clean: the retired bootstrap runner is gone
# ---------------------------------------------------------------------------


def test_no_retired_bootstrap_runner_references_remain():
    """The retired single-command bootstrap runner is gone from tools/camp."""
    # Split the needle so this test file does not match itself.
    needle = "_run_" + "bootstrap"
    camp_root = _REPO_ROOT / "tools" / "camp"
    hits = [
        str(p)
        for p in camp_root.rglob("*.py")
        if needle in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert hits == [], f"retired bootstrap runner still referenced in: {hits}"
