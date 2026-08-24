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


def _member_wt(group_name: str, slug: str, member: str, env: dict[str, str]) -> Path:
    from camp.group.resolve import central_state_dir

    return central_state_dir(group_name, env=env) / "worktrees" / slug / member


# ---------------------------------------------------------------------------
# provision_member / cmd_setup_group path
# ---------------------------------------------------------------------------


def _mcp_config_task() -> dict:
    """The mcp-config recipe shape (from trailhead.toml), unstubbed — a real
    `python3` invocation so the copy behavior itself is exercised end-to-end."""
    return {
        "name": "mcp-config",
        "phase": "provision",
        "required": False,
        "timeout_seconds": None,
        "steps": [
            {
                "name": "copy",
                "cmd": [
                    "python3",
                    "-c",
                    "import pathlib, shutil, sys\n"
                    "src = pathlib.Path(sys.argv[1])\n"
                    "if src.is_file():\n"
                    "    shutil.copy(src, sys.argv[2])\n",
                    "{repo_root}/.mcp.json",
                    "{worktree}/.mcp.json",
                ],
            }
        ],
    }


def test_mcp_config_task_copies_mcp_json_into_worktree(tmp_path):
    """A repo root holding `.mcp.json` yields a worktree holding an identical
    copy after bring-up — the config that a fresh checkout never carries
    (`.mcp.json` is gitignored) arrives via the provision task instead."""
    from camp.provision.provision import seed_pending_workspace
    from camp.provision.lifecycle import cmd_setup_group
    from camp.group.manifest import read_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    mcp_json = repo / ".mcp.json"
    mcp_json.write_text('{"mcpServers": {"code-review-graph": {}}}')
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "mcpg",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [_mcp_config_task()],
            }
        ],
    )

    seed_pending_workspace(group, "s", env=env)
    result = cmd_setup_group(group, "s", env=env)

    assert result["members"]["repo"]["provision_state"] == "ready"
    wt_mcp_json = _member_wt("mcpg", "s", "repo", env) / ".mcp.json"
    assert wt_mcp_json.read_text() == mcp_json.read_text()

    data = read_central_manifest(_manifest_path("mcpg", "s", env))
    entry = data["members"][0]
    assert entry["tasks"]["mcp-config"]["state"] == "ok"


def test_mcp_config_task_missing_source_does_not_fail_provisioning(tmp_path):
    """A repo root with no `.mcp.json` no-ops the copy step instead of
    failing it, so provisioning succeeds AND the task itself is recorded
    "ok" rather than "failed" — the point of the no-op is to avoid the
    permanent `camp status` warning a persistently-"failed" optional task
    would otherwise print on every SessionStart reconcile."""
    from camp.provision.provision import seed_pending_workspace
    from camp.provision.lifecycle import cmd_setup_group
    from camp.group.manifest import read_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "mcpg2",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [_mcp_config_task()],
            }
        ],
    )

    seed_pending_workspace(group, "s", env=env)
    result = cmd_setup_group(group, "s", env=env)

    assert result["members"]["repo"]["provision_state"] == "ready"
    wt_mcp_json = _member_wt("mcpg2", "s", "repo", env) / ".mcp.json"
    assert not wt_mcp_json.exists()

    data = read_central_manifest(_manifest_path("mcpg2", "s", env))
    entry = data["members"][0]
    assert entry["tasks"]["mcp-config"]["state"] == "ok"


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
# setup retry on already-READY members (outstanding-task re-run)
#
# A ready member may still carry a failed/never-run OPTIONAL task (an optional
# failure leaves the member ready). `camp setup` re-runs those outstanding tasks
# in place, distinguishing three outcomes per member: "none" (all ok — a true
# no-op, no re-provision), "fixed" (retry cleared the failure), "still-failing".
# ---------------------------------------------------------------------------


def test_setup_reruns_outstanding_task_on_ready_member_and_clears_on_success(tmp_path):
    """A ready member with a failed optional task re-runs ONLY that task on the
    next setup (an already-ok task is skipped), clears it to ok, and reports the
    retry outcome as "fixed"."""
    from camp.provision.provision import seed_pending_workspace
    from camp.provision.lifecycle import cmd_setup_group
    from camp.group.manifest import read_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    seed_runs = tmp_path / "seed_runs"
    sentinel = tmp_path / "ready.flag"
    group = _make_group(
        "readyfix",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [
                    _provision_task("seed", ["sh", "-c", f"echo x >> {seed_runs}"]),
                    _provision_task("graphify", ["sh", "-c", f"test -f {sentinel}"]),
                ],
            }
        ],
    )

    seed_pending_workspace(group, "s", env=env)
    # First setup: seed ok; graphify (optional) fails → member stays ready.
    r1 = cmd_setup_group(group, "s", env=env)
    assert r1["members"]["repo"]["provision_state"] == "ready"
    entry = read_central_manifest(_manifest_path("readyfix", "s", env))["members"][0]
    assert entry["tasks"]["graphify"]["state"] == "failed"

    # The outstanding task can now succeed.
    sentinel.write_text("go\n")

    # Second setup on the READY member: re-runs ONLY the outstanding task; the
    # already-ok seed is skipped (run-once); graphify clears to ok.
    r2 = cmd_setup_group(group, "s", env=env)
    assert r2["members"]["repo"] == {"provision_state": "ready", "retry": "fixed"}
    assert seed_runs.read_text().count("x") == 1

    entry = read_central_manifest(_manifest_path("readyfix", "s", env))["members"][0]
    assert entry["tasks"]["seed"]["state"] == "ok"
    assert entry["tasks"]["graphify"]["state"] == "ok"


def test_setup_ready_member_all_tasks_ok_is_noop(tmp_path, monkeypatch):
    """A ready member whose tasks are all ok is a true no-op: setup does not
    invoke the per-member provision (no git fetch, no re-run) and reports the
    retry outcome as "none"."""
    import camp.provision.provision as provision
    from camp.provision.provision import seed_pending_workspace
    from camp.provision.lifecycle import cmd_setup_group

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    seed_runs = tmp_path / "seed_runs"
    group = _make_group(
        "readynoop",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [_provision_task("seed", ["sh", "-c", f"echo x >> {seed_runs}"])],
            }
        ],
    )

    seed_pending_workspace(group, "s", env=env)
    first = cmd_setup_group(group, "s", env=env)  # pending → ready, seed ok
    assert seed_runs.read_text().count("x") == 1
    # A pending member provisioned normally does NOT carry a retry outcome.
    assert "retry" not in first["members"]["repo"]

    # Track whether the per-member provision runs at all on the second pass.
    calls: list[str] = []
    real = provision.provision_member

    def tracking(group, slug, member, *, completed=None, env):
        calls.append(member["name"])
        return real(group, slug, member, completed=completed, env=env)

    monkeypatch.setattr(provision, "provision_member", tracking)

    result = cmd_setup_group(group, "s", env=env)
    assert calls == [], f"a no-op ready member must not be re-provisioned: {calls}"
    assert result["members"]["repo"] == {"provision_state": "ready", "retry": "none"}
    assert seed_runs.read_text().count("x") == 1


def test_setup_ready_member_task_still_failing(tmp_path):
    """A ready member whose outstanding optional task fails again on retry keeps
    provision_state ready and reports the retry outcome as "still-failing"."""
    from camp.provision.provision import seed_pending_workspace
    from camp.provision.lifecycle import cmd_setup_group
    from camp.group.manifest import read_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "readystill",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [_provision_task("graphify", ["false"], required=False)],
            }
        ],
    )

    seed_pending_workspace(group, "s", env=env)
    cmd_setup_group(group, "s", env=env)  # ready with graphify failed (optional)

    result = cmd_setup_group(group, "s", env=env)
    assert result["members"]["repo"] == {
        "provision_state": "ready",
        "retry": "still-failing",
    }

    entry = read_central_manifest(_manifest_path("readystill", "s", env))["members"][0]
    assert entry["tasks"]["graphify"]["state"] == "failed"


def test_setup_ready_member_retry_timeout_does_not_demote(tmp_path, monkeypatch, capsys):
    """A transient git-fetch timeout hit while retrying an already-ready member's
    outstanding optional task must NOT demote the member to failed — it stays
    ready, the retry outcome is "still-failing", and the reason is warned."""
    import camp.provision.provision as provision
    from camp.provision.provision import seed_pending_workspace
    from camp.provision.lifecycle import cmd_setup_group
    from camp.group.manifest import read_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "readytimeout",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [_provision_task("graphify", ["false"], required=False)],
            }
        ],
    )

    seed_pending_workspace(group, "s", env=env)
    cmd_setup_group(group, "s", env=env)  # ready with graphify failed (optional)

    def boom(group, slug, member, *, completed=None, env):
        raise subprocess.TimeoutExpired(cmd=["git", "fetch"], timeout=5)

    monkeypatch.setattr(provision, "provision_member", boom)

    result = cmd_setup_group(group, "s", env=env)
    assert result["members"]["repo"] == {
        "provision_state": "ready",
        "retry": "still-failing",
    }

    entry = read_central_manifest(_manifest_path("readytimeout", "s", env))["members"][0]
    assert entry["provision_state"] == "ready"

    err = capsys.readouterr().err
    assert "repo" in err
    assert "timeout" in err.lower()


def test_setup_ready_member_retry_generic_exception_does_not_demote(
    tmp_path, monkeypatch, capsys
):
    """A generic exception (e.g. a git command failure) hit while retrying an
    already-ready member's outstanding optional task must NOT demote the member
    to failed — same "still-failing"-but-ready contract as a timeout."""
    import camp.provision.provision as provision
    from camp.provision.provision import seed_pending_workspace
    from camp.provision.lifecycle import cmd_setup_group
    from camp.group.manifest import read_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "readygeneric",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [_provision_task("graphify", ["false"], required=False)],
            }
        ],
    )

    seed_pending_workspace(group, "s", env=env)
    cmd_setup_group(group, "s", env=env)  # ready with graphify failed (optional)

    def boom(group, slug, member, *, completed=None, env):
        raise RuntimeError("git command failed")

    monkeypatch.setattr(provision, "provision_member", boom)

    result = cmd_setup_group(group, "s", env=env)
    assert result["members"]["repo"] == {
        "provision_state": "ready",
        "retry": "still-failing",
    }

    entry = read_central_manifest(_manifest_path("readygeneric", "s", env))["members"][0]
    assert entry["provision_state"] == "ready"

    err = capsys.readouterr().err
    assert "repo" in err
    assert "git command failed" in err


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
# reconcile_worktree: provision_state/activated carry-forward
# ---------------------------------------------------------------------------


def test_reconcile_preserves_provision_state_and_activated(tmp_path):
    """A second reconcile_worktree run never regresses provision_state/activated
    set by cmd_setup_group/activation.py — it only rebuilds worktree bookkeeping
    and tasks, so prior provision_state/activated must survive unchanged."""
    from camp.provision.reconcile import reconcile_worktree
    from camp.group.manifest import read_central_manifest, write_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "carryg",
        [{"name": "repo", "repo_root": str(repo), "base": "origin/main"}],
    )

    reconcile_worktree(group, "s", env=env)

    mpath = _manifest_path("carryg", "s", env)
    data = read_central_manifest(mpath)
    data["members"][0]["provision_state"] = "ready"
    data["members"][0]["activated"] = True
    write_central_manifest(mpath, data)

    reconcile_worktree(group, "s", env=env)

    entry = read_central_manifest(mpath)["members"][0]
    assert entry["provision_state"] == "ready"
    assert entry["activated"] is True


def test_reconcile_first_run_has_no_provision_state_or_activated_key(tmp_path):
    """A member with no prior manifest entry (first-ever reconcile) gets no
    provision_state/activated key — unchanged from today's first-run shape."""
    from camp.provision.reconcile import reconcile_worktree
    from camp.group.manifest import read_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "firstrung",
        [{"name": "repo", "repo_root": str(repo), "base": "origin/main"}],
    )

    reconcile_worktree(group, "s", env=env)

    entry = read_central_manifest(_manifest_path("firstrung", "s", env))["members"][0]
    assert "provision_state" not in entry
    assert "activated" not in entry


def test_reconcile_preserves_failed_provision_state_and_reason(tmp_path):
    """A member whose prior provision_state was "failed" (with a reason) keeps
    both across a reconcile run that doesn't touch provision itself."""
    from camp.provision.reconcile import reconcile_worktree
    from camp.group.manifest import read_central_manifest, write_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "failedg",
        [{"name": "repo", "repo_root": str(repo), "base": "origin/main"}],
    )

    reconcile_worktree(group, "s", env=env)

    mpath = _manifest_path("failedg", "s", env)
    data = read_central_manifest(mpath)
    data["members"][0]["provision_state"] = "failed"
    data["members"][0]["reason"] = "migrate task failed"
    write_central_manifest(mpath, data)

    reconcile_worktree(group, "s", env=env)

    entry = read_central_manifest(mpath)["members"][0]
    assert entry["provision_state"] == "failed"
    assert entry["reason"] == "migrate task failed"


def test_reconcile_carries_provision_state_activated_and_tasks_together(tmp_path):
    """A single reconcile run carries forward provision_state, activated, AND
    the tasks map together in the same member entry, none clobbering the others."""
    from camp.provision.reconcile import reconcile_worktree
    from camp.group.manifest import read_central_manifest, write_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "combinedg",
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

    mpath = _manifest_path("combinedg", "s", env)
    data = read_central_manifest(mpath)
    data["members"][0]["provision_state"] = "ready"
    data["members"][0]["activated"] = True
    write_central_manifest(mpath, data)

    reconcile_worktree(group, "s", env=env)

    entry = read_central_manifest(mpath)["members"][0]
    assert entry["provision_state"] == "ready"
    assert entry["activated"] is True
    assert entry["tasks"] == {"ok": {"state": "ok"}}


# ---------------------------------------------------------------------------
# reconcile_worktree: work_state (work-readiness) fact
# ---------------------------------------------------------------------------


def test_reconcile_member_with_no_activate_task_reports_not_applicable_work_state(tmp_path):
    """A member that declares no activate-phase task reports the "not
    applicable" work value on the very first reconcile, rather than sitting at
    "pending" forever waiting for activate-phase work that will never come."""
    from camp.provision.reconcile import reconcile_worktree
    from camp.group.manifest import read_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "noactivateg",
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

    entry = read_central_manifest(_manifest_path("noactivateg", "s", env))["members"][0]
    assert entry["work_state"] == "not-applicable"


def test_reconcile_member_with_activate_task_leaves_work_state_absent_on_first_run(tmp_path):
    """A member that DOES declare an activate-phase task gets no work_state key
    on the first reconcile (mirrors provision_state/activated's first-run
    absence) — reconcile_worktree never runs activate-phase tasks itself, so it
    has nothing to report yet, and must not raise deciding that."""
    from camp.provision.reconcile import reconcile_worktree
    from camp.group.manifest import read_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "hasactivateg",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [_provision_task("dep-install", ["true"], phase="activate")],
            }
        ],
    )

    reconcile_worktree(group, "s", env=env)

    entry = read_central_manifest(_manifest_path("hasactivateg", "s", env))["members"][0]
    assert "work_state" not in entry


def test_reconcile_carries_forward_work_state_set_to_ready(tmp_path):
    """The carry-forward regression test: a work_state set to "ready" (as
    activation.py would set it) survives a reconcile that doesn't touch
    work-readiness itself — reconcile rebuilds each member entry from a
    hardcoded key tuple, and an unlisted field is silently dropped on the next
    reconcile. This is the exact bug being guarded against."""
    from camp.provision.reconcile import reconcile_worktree
    from camp.group.manifest import read_central_manifest, write_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    group = _make_group(
        "workcarryg",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [_provision_task("dep-install", ["true"], phase="activate")],
            }
        ],
    )

    reconcile_worktree(group, "s", env=env)

    mpath = _manifest_path("workcarryg", "s", env)
    data = read_central_manifest(mpath)
    data["members"][0]["work_state"] = "ready"
    write_central_manifest(mpath, data)

    reconcile_worktree(group, "s", env=env)

    entry = read_central_manifest(mpath)["members"][0]
    assert entry["work_state"] == "ready"


# ---------------------------------------------------------------------------
# over-budget: persists verbatim through the manifest projection
# ---------------------------------------------------------------------------


def test_tasks_map_from_results_persists_over_budget_verbatim_skipped_still_ok():
    """_tasks_map_from_results stops collapsing every non-failed result into
    "ok": an over-budget result must survive verbatim, or a caller can never
    tell "ran out of time" apart from "succeeded" and the task never gets
    retried anywhere. A skipped result still persists as "ok" (unchanged)."""
    from camp.provision.reconcile import _tasks_map_from_results
    from camp.provision.tasks import TaskResult

    results = [
        TaskResult(name="graph-build", state="over-budget"),
        TaskResult(name="mcp-config", state="skipped"),
    ]

    out = _tasks_map_from_results(results)

    assert out["graph-build"] == {"state": "over-budget"}
    assert out["mcp-config"] == {"state": "ok"}


def test_completed_from_tasks_map_over_budget_direction_flag():
    """The projection from a persisted `tasks` map back onto the runner's
    `completed` shape must not silently collapse "over-budget" the same way in
    both directions: over_budget_as_ok=True (the boot-budget-constrained
    SessionStart hook path) makes an over-budget task skip-worthy, exactly like
    "ok"; the default (False — `camp setup`'s retry path) leaves it as its own
    literal, non-"ok" state, so the task is retry-worthy there."""
    from camp.provision.reconcile import _completed_from_tasks_map

    tasks_map = {"graph-build": {"state": "over-budget"}}

    assert _completed_from_tasks_map(tasks_map, over_budget_as_ok=True) == {
        "graph-build": "ok"
    }
    assert _completed_from_tasks_map(tasks_map) == {"graph-build": "over-budget"}


def test_reconcile_hook_path_skips_over_budget_task_without_reexecuting(tmp_path):
    """reconcile_worktree (the SessionStart hook path) treats a task recorded
    over-budget as skip-worthy: it does not re-run it within the tight boot
    window, and its persisted state survives as "over-budget" rather than
    being silently normalized to "ok" by the skip itself."""
    from camp.provision.reconcile import reconcile_worktree
    from camp.group.manifest import read_central_manifest, write_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    runs = tmp_path / "runs"
    group = _make_group(
        "hookskipg",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [_provision_task("graph-build", ["sh", "-c", f"echo x >> {runs}"])],
            }
        ],
    )

    reconcile_worktree(group, "s", env=env)
    assert runs.read_text().count("x") == 1  # the task's real first run

    mpath = _manifest_path("hookskipg", "s", env)
    data = read_central_manifest(mpath)
    data["members"][0]["tasks"] = {"graph-build": {"state": "over-budget"}}
    write_central_manifest(mpath, data)

    reconcile_worktree(group, "s", env=env)

    assert runs.read_text().count("x") == 1, "hook path must not re-execute an over-budget task"
    entry = read_central_manifest(mpath)["members"][0]
    assert entry["tasks"]["graph-build"]["state"] == "over-budget"


def test_setup_retries_task_recorded_over_budget(tmp_path):
    """camp setup treats a task recorded over-budget as retry-worthy — it
    re-runs (and, on success, clears) rather than skipping it forever."""
    from camp.provision.provision import seed_pending_workspace
    from camp.provision.lifecycle import cmd_setup_group
    from camp.group.manifest import read_central_manifest, write_central_manifest

    repo = tmp_path / "repo"
    _init_git_repo(repo)
    env = _camp_state_env(tmp_path)
    runs = tmp_path / "runs"
    group = _make_group(
        "setupretryg",
        [
            {
                "name": "repo",
                "repo_root": str(repo),
                "base": "origin/main",
                "tasks": [_provision_task("graph-build", ["sh", "-c", f"echo x >> {runs}"])],
            }
        ],
    )

    seed_pending_workspace(group, "s", env=env)
    mpath = _manifest_path("setupretryg", "s", env)
    data = read_central_manifest(mpath)
    data["members"][0]["provision_state"] = "ready"
    data["members"][0]["tasks"] = {"graph-build": {"state": "over-budget"}}
    write_central_manifest(mpath, data)

    cmd_setup_group(group, "s", env=env)

    assert runs.read_text().count("x") == 1, "camp setup must re-run an over-budget task"
    entry = read_central_manifest(mpath)["members"][0]
    assert entry["tasks"]["graph-build"]["state"] == "ok"


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
