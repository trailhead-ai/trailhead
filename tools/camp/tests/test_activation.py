"""Tests for activation.py — camp activate <member>.

Test contract:
- camp activate <member> returns WITHOUT waiting for activate-phase tasks:
  outstanding work is handed to the detached provisioner
  (spawn_detached_provisioner), never run inline.
- The member is marked activated immediately, regardless of task outcome — the
  operator gets the member doc and can work in the worktree while tasks are
  outstanding.
- Two concurrent activations of the same member run its tasks once — enforced
  by a per-(slug, member) lockfile guard released by the OS when its holder
  dies (crash case: a killed holder leaves the guard free, never wedged).
- The guard's lockfile is never unlinked without the lock held, and an acquire
  re-checks the inode.
- Activate-phase tasks are run-once-on-success and retried-on-failure against
  the manifest's PERSISTED per-task state (not an empty `completed` map): an
  "ok" task is skipped, a "failed" task is retried with its cleanup command
  first. A required task's failure marks the member's work_state "failed" and
  skips that member's remaining tasks.
- The feedback line distinguishes: tasks freshly queued, already in progress,
  already work-ready, work failed (retrying), and "no activate-phase task
  declared".
- camp activate <pending-member> → "still provisioning" message + retry hint,
  tasks NOT run.
- camp activate <failed-member> → names the failure + retry command.
- A legacy dep-install hook config still executes on the detached run (via
  load_group's normalization into an implicit required activate-phase task).
- malformed/unknown hook kind in config → GroupConfigError naming member + kind.
- group_config parses + validates the activation-hook block: string-list
  enforcement, PLUS strip-and-reject empty/whitespace-only argv tokens.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_manifest(
    tmp_path: Path,
    slug: str,
    group_name: str,
    members: list[dict],
) -> Path:
    """Write a minimal manifest.json and return its path."""
    manifest_dir = tmp_path / "camp" / group_name / "worktrees" / slug
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "group": group_name,
                "slug": slug,
                "branch": f"worktree-{slug}",
                "members": members,
            }
        )
    )
    return manifest_path


def _env(tmp_path: Path) -> dict[str, str]:
    """Return a CAMP_STATE_DIR env override pointing at tmp_path."""
    return {"CAMP_STATE_DIR": str(tmp_path / "camp")}


def _activate_task(name: str, cmds: list[list[str]], *, required: bool = True) -> dict:
    """Build a config-resolved activate-phase task (steps as {"cmd": argv})."""
    return {
        "name": name,
        "phase": "activate",
        "required": required,
        "steps": [{"cmd": cmd} for cmd in cmds],
    }


def _make_group(
    group_name: str,
    member_name: str,
    tasks: list[dict] | None = None,
    harness: dict | None = None,
) -> dict:
    """Build a minimal group config dict with optional resolved tasks."""
    member = {
        "name": member_name,
        "repo_root": "/tmp/fake-repo",
        "base": "origin/main",
        "tasks": tasks or [],
    }
    group = {
        "group": {"name": group_name},
        "members": [member],
        "branch_pattern": "worktree-{slug}",
        "shared_vaults": [],
    }
    if harness is not None:
        group["harness"] = harness
    return group


# ---------------------------------------------------------------------------
# activate_member: pending member → "still provisioning" + hint, no tasks
# ---------------------------------------------------------------------------


def test_activate_pending_prints_provisioning_message(tmp_path: Path) -> None:
    """A pending member → 'still provisioning' message + retry hint; tasks NOT run."""
    from camp.provision.activation import activate_member, MemberNotReadyError

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "pending",
            }
        ],
    )

    group = _make_group(group_name, member_name)
    env = _env(tmp_path)

    with pytest.raises(MemberNotReadyError) as exc_info:
        activate_member(group, slug, member_name, env=env)

    msg = str(exc_info.value)
    assert "still provisioning" in msg.lower() or "provisioning" in msg.lower()
    assert "camp status" in msg or "camp setup" in msg


def test_activate_pending_does_not_run_tasks(tmp_path: Path) -> None:
    """A pending member triggers MemberNotReadyError before any task step runs."""
    from camp.provision.activation import activate_member, MemberNotReadyError

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "pending",
            }
        ],
    )

    group = _make_group(
        group_name,
        member_name,
        tasks=[_activate_task("dep-install", [["echo", "hook-ran"]])],
    )
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        with pytest.raises(MemberNotReadyError):
            activate_member(group, slug, member_name, env=env)
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# activate_member: failed member → names failure + retry command
# ---------------------------------------------------------------------------


def test_activate_failed_names_failure_and_retry(tmp_path: Path) -> None:
    """A failed member → MemberNotReadyError naming the failure and retry command."""
    from camp.provision.activation import activate_member, MemberNotReadyError

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)
    failure_reason = "git fetch timed out after 30s"

    _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "failed",
                "reason": failure_reason,
            }
        ],
    )

    group = _make_group(group_name, member_name)
    env = _env(tmp_path)

    with pytest.raises(MemberNotReadyError) as exc_info:
        activate_member(group, slug, member_name, env=env)

    msg = str(exc_info.value)
    assert failure_reason in msg
    assert "camp setup" in msg or "retry" in msg.lower()


# ---------------------------------------------------------------------------
# activate_member: ready member — never runs tasks inline, marks activated
# immediately, prints CLAUDE.md
# ---------------------------------------------------------------------------


def test_activate_ready_does_not_run_tasks_inline(tmp_path: Path) -> None:
    """camp activate never runs activate-phase task steps inline — outstanding
    work is handed to the detached provisioner, not executed synchronously."""
    from camp.provision.activation import activate_member

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
            }
        ],
    )

    tasks = [
        _activate_task("dep-install", [["npm", "install"], ["pip", "install", "-e", "."]])
    ]
    group = _make_group(group_name, member_name, tasks=tasks)
    env = _env(tmp_path)

    with patch("camp.provision.provision.spawn_detached_provisioner") as mock_spawn:
        with patch("subprocess.run") as mock_run:
            activate_member(group, slug, member_name, env=env)

    mock_run.assert_not_called()
    mock_spawn.assert_called_once()


def test_activate_ready_marks_activated_before_tasks_run(tmp_path: Path) -> None:
    """The member is marked activated immediately — before any activate-phase
    task has had a chance to run — so the operator gets the doc right away."""
    from camp.provision.activation import activate_member
    from camp.group.manifest import read_central_manifest

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    mpath = _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
            }
        ],
    )

    tasks = [_activate_task("dep-install", [["npm", "install"]])]
    group = _make_group(group_name, member_name, tasks=tasks)
    env = _env(tmp_path)

    with patch("camp.provision.provision.spawn_detached_provisioner"):
        activate_member(group, slug, member_name, env=env)

    data = read_central_manifest(mpath)
    member_entry = next(m for m in data["members"] if m["name"] == member_name)
    assert member_entry.get("activated") is True


def test_activate_ready_prints_member_claude_md(tmp_path: Path, capsys) -> None:
    """activate_member prints the member's CLAUDE.md content to stdout."""
    from camp.provision.activation import activate_member

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    claude_md_content = "# Member CLAUDE.md\n\nThis is the member doc.\n"
    (wt_path / "CLAUDE.md").write_text(claude_md_content)

    _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
            }
        ],
    )

    group = _make_group(group_name, member_name, harness={"inject": "stdout"})
    env = _env(tmp_path)

    activate_member(group, slug, member_name, env=env)

    captured = capsys.readouterr()
    assert claude_md_content in captured.out


def test_activate_ready_prints_fallback_when_no_claude_md(tmp_path: Path, capsys) -> None:
    """When no CLAUDE.md exists, activate_member still prints something useful."""
    from camp.provision.activation import activate_member

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)
    # No CLAUDE.md written

    _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
            }
        ],
    )

    group = _make_group(group_name, member_name, harness={"inject": "stdout"})
    env = _env(tmp_path)

    activate_member(group, slug, member_name, env=env)

    captured = capsys.readouterr()
    # Should mention the member name at minimum
    assert member_name in captured.out or member_name in captured.err


def test_activate_ready_marks_activated_in_manifest(tmp_path: Path) -> None:
    """After activate_member succeeds, the manifest member has activated=true."""
    from camp.provision.activation import activate_member
    from camp.group.manifest import read_central_manifest

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    mpath = _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
            }
        ],
    )

    group = _make_group(group_name, member_name)
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        activate_member(group, slug, member_name, env=env)

    data = read_central_manifest(mpath)
    member_entry = next(m for m in data["members"] if m["name"] == member_name)
    assert member_entry.get("activated") is True


# ---------------------------------------------------------------------------
# run_activate_tasks_in_background — the detached run's actual task execution
# ---------------------------------------------------------------------------


def test_background_run_records_task_state_and_work_ready(tmp_path: Path) -> None:
    """run_activate_tasks_in_background: a successful task records state 'ok'
    in the manifest tasks map, and work_state becomes 'ready'."""
    from camp.provision.activation import run_activate_tasks_in_background
    from camp.group.manifest import read_central_manifest

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    mpath = _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
            }
        ],
    )

    tasks = [_activate_task("dep-install", [["npm", "install"]])]
    group = _make_group(group_name, member_name, tasks=tasks)
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        run_activate_tasks_in_background(group, slug, member_name, env=env)

    assert mock_run.call_count == 1
    assert mock_run.call_args_list[0][0][0] == ["npm", "install"]
    assert mock_run.call_args_list[0][1].get("shell") is not True

    data = read_central_manifest(mpath)
    member_entry = next(m for m in data["members"] if m["name"] == member_name)
    assert member_entry["tasks"]["dep-install"]["state"] == "ok"
    assert member_entry["work_state"] == "ready"


def test_background_run_skips_task_recorded_ok(tmp_path: Path) -> None:
    """A task whose persisted state is 'ok' is skipped on re-activation —
    the detached run reads persisted task state, not an empty completed map."""
    from camp.provision.activation import run_activate_tasks_in_background

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
                "activated": True,
                "tasks": {"dep-install": {"state": "ok"}},
            }
        ],
    )

    tasks = [_activate_task("dep-install", [["npm", "install"]])]
    group = _make_group(group_name, member_name, tasks=tasks)
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        run_activate_tasks_in_background(group, slug, member_name, env=env)

    mock_run.assert_not_called()


def test_background_run_retries_failed_task_with_cleanup_first(tmp_path: Path) -> None:
    """One recorded 'failed' task is retried, with its cleanup command running
    first — this is the path that lets a partial `node_modules` recover."""
    from camp.provision.activation import run_activate_tasks_in_background

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
                "activated": True,
                "tasks": {"dep-install": {"state": "failed"}},
            }
        ],
    )

    task = _activate_task("dep-install", [["npm", "ci"]])
    task["cleanup"] = ["rm", "-rf", "node_modules"]
    group = _make_group(group_name, member_name, tasks=[task])
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        run_activate_tasks_in_background(group, slug, member_name, env=env)

    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0][0][0] == ["rm", "-rf", "node_modules"]
    assert mock_run.call_args_list[1][0][0] == ["npm", "ci"]


def test_background_run_honors_timeout_seconds(tmp_path: Path) -> None:
    """Activate-phase task execution honours each task's timeout_seconds."""
    from camp.provision.activation import run_activate_tasks_in_background

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
            }
        ],
    )

    task = _activate_task("dep-install", [["npm", "install"]])
    task["timeout_seconds"] = 7
    group = _make_group(group_name, member_name, tasks=[task])
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        run_activate_tasks_in_background(group, slug, member_name, env=env)

    assert mock_run.call_args_list[0][1]["timeout"] == 7


def test_background_run_required_failure_marks_work_failed_and_skips_remaining(
    tmp_path: Path,
) -> None:
    """A required task's failure marks the member's work_state 'failed' and
    skips that member's remaining activate-phase tasks."""
    from camp.provision.activation import run_activate_tasks_in_background
    from camp.group.manifest import read_central_manifest

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    mpath = _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
            }
        ],
    )

    tasks = [
        _activate_task("first", [["false"]], required=True),
        _activate_task("second", [["echo", "never"]], required=True),
    ]
    group = _make_group(group_name, member_name, tasks=tasks)
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        run_activate_tasks_in_background(group, slug, member_name, env=env)

    assert mock_run.call_count == 1
    assert mock_run.call_args_list[0][0][0] == ["false"]

    data = read_central_manifest(mpath)
    member_entry = next(m for m in data["members"] if m["name"] == member_name)
    assert member_entry["work_state"] == "failed"
    assert member_entry["tasks"]["first"]["state"] == "failed"
    assert "second" not in member_entry.get("tasks", {})


def test_activate_already_work_ready_does_not_spawn(tmp_path: Path) -> None:
    """Re-activating a member whose activate-phase tasks are ALL persisted
    'ok' (work_state 'ready') skips it — no detached run is spawned; doc is
    still printed. Idempotency now keys on persisted task state, not the
    'activated' flag alone."""
    from camp.provision.activation import activate_member

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    claude_md_content = "# Member Doc\n"
    (wt_path / "CLAUDE.md").write_text(claude_md_content)

    _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
                "activated": True,
                "work_state": "ready",
                "tasks": {"dep-install": {"state": "ok"}},
            }
        ],
    )

    tasks = [_activate_task("dep-install", [["npm", "install"]])]
    group = _make_group(group_name, member_name, tasks=tasks, harness={"inject": "stdout"})
    env = _env(tmp_path)

    import contextlib
    import io

    out = io.StringIO()
    with patch("camp.provision.provision.spawn_detached_provisioner") as mock_spawn:
        with contextlib.redirect_stdout(out):
            activate_member(group, slug, member_name, env=env)

    mock_spawn.assert_not_called()
    # Doc was still printed
    assert claude_md_content in out.getvalue()


def test_activate_ready_reactivate_reprints_doc(tmp_path: Path, capsys) -> None:
    """Re-activating an activated member still prints the CLAUDE.md to stdout."""
    from camp.provision.activation import activate_member

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    claude_md_content = "# Already Activated Doc\n"
    (wt_path / "CLAUDE.md").write_text(claude_md_content)

    _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
                "activated": True,
            }
        ],
    )

    group = _make_group(group_name, member_name, harness={"inject": "stdout"})
    env = _env(tmp_path)

    activate_member(group, slug, member_name, env=env)

    captured = capsys.readouterr()
    assert claude_md_content in captured.out


# ---------------------------------------------------------------------------
# Legacy dep-install hook config still executes on the detached run (normalized).
# ---------------------------------------------------------------------------


def test_legacy_dep_install_executes_on_background_run(tmp_path: Path) -> None:
    """A legacy [[members.hooks]] dep-install block, normalized by load_group into
    an implicit required activate-phase task, still runs on the detached run."""
    from camp.group.config import load_group
    from camp.provision.activation import run_activate_tasks_in_background
    from camp.group.manifest import read_central_manifest

    group_name = "testgroup"
    member_name = "myrepo"
    slug = "my-slug"

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/fake-repo"

[[members.hooks]]
kind = "dep-install"
cmd = ["echo", "installing"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    group = load_group(f)

    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    mpath = _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
                "activated": True,
            }
        ],
    )
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        run_activate_tasks_in_background(group, slug, member_name, env=env)

    assert mock_run.call_count == 1
    assert mock_run.call_args_list[0][0][0] == ["echo", "installing"]

    data = read_central_manifest(mpath)
    member_entry = next(m for m in data["members"] if m["name"] == member_name)
    assert member_entry["tasks"]["dep-install"]["state"] == "ok"
    assert member_entry["work_state"] == "ready"


# ---------------------------------------------------------------------------
# Optional activate-task failure — work_state stays 'ready', warns on stderr.
# ---------------------------------------------------------------------------


def test_optional_task_failure_stays_work_ready_and_warns(tmp_path: Path, capsys) -> None:
    """An optional activate-task failure warns on stderr, records the failed
    state, and work_state still becomes 'ready' (activation proceeds)."""
    from camp.provision.activation import run_activate_tasks_in_background
    from camp.group.manifest import read_central_manifest

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    mpath = _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
                "activated": True,
            }
        ],
    )

    tasks = [_activate_task("optional-task", [["false"]], required=False)]
    group = _make_group(group_name, member_name, tasks=tasks, harness={"inject": "stdout"})
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="nope")
        run_activate_tasks_in_background(group, slug, member_name, env=env)

    data = read_central_manifest(mpath)
    member_entry = next(m for m in data["members"] if m["name"] == member_name)
    assert member_entry["work_state"] == "ready"
    assert member_entry["tasks"]["optional-task"]["state"] == "failed"

    captured = capsys.readouterr()
    assert "optional-task" in captured.err
    assert member_name in captured.err
    assert "camp status" in captured.err


# ---------------------------------------------------------------------------
# The four-state feedback line: queued / already in progress / already
# work-ready / failed (retrying) — plus the no-activate-task-declared case.
# ---------------------------------------------------------------------------


def _ready_member_with_tasks(
    tmp_path: Path, *, work_state: str | None = None, tasks_map: dict | None = None
) -> tuple[dict, str, str, str, Path]:
    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    entry = {
        "name": member_name,
        "repo_root": "/tmp/fake-repo",
        "worktree_path": str(wt_path),
        "provision_state": "ready",
        "activated": True,
    }
    if work_state is not None:
        entry["work_state"] = work_state
    if tasks_map is not None:
        entry["tasks"] = tasks_map

    _make_manifest(tmp_path, slug, group_name, [entry])

    tasks = [_activate_task("dep-install", [["npm", "install"]])]
    group = _make_group(group_name, member_name, tasks=tasks, harness={"inject": "stdout"})
    return group, group_name, member_name, slug, wt_path


def test_feedback_queued_on_first_activation(tmp_path: Path, capsys) -> None:
    """A member with tasks outstanding (never run before) gets a 'queued'
    feedback line and a detached run is spawned."""
    from camp.provision.activation import activate_member

    group, _group_name, member_name, slug, _wt = _ready_member_with_tasks(tmp_path)
    env = _env(tmp_path)

    with patch("camp.provision.provision.spawn_detached_provisioner") as mock_spawn:
        activate_member(group, slug, member_name, env=env)

    mock_spawn.assert_called_once()
    captured = capsys.readouterr()
    assert "queued" in captured.err.lower()
    assert "already in progress" not in captured.err.lower()
    assert "already work-ready" not in captured.err.lower()


def test_feedback_already_in_progress_when_guard_held(tmp_path: Path, capsys) -> None:
    """A member whose guard is currently held by another run gets an
    'already in progress' feedback line, and no second run is spawned."""
    import camp.provision.activation as activation

    group, _group_name, member_name, slug, wt_path = _ready_member_with_tasks(tmp_path)
    env = _env(tmp_path)

    ws_dir = wt_path.parent
    lock_path = activation.member_guard_lock_path(ws_dir, member_name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder_fd = open(str(lock_path), "w")
    fcntl.flock(holder_fd.fileno(), fcntl.LOCK_EX)
    try:
        with patch("camp.provision.provision.spawn_detached_provisioner") as mock_spawn:
            activation.activate_member(group, slug, member_name, env=env)
        mock_spawn.assert_not_called()
    finally:
        fcntl.flock(holder_fd.fileno(), fcntl.LOCK_UN)
        holder_fd.close()

    captured = capsys.readouterr()
    assert "already in progress" in captured.err.lower()


def test_feedback_already_work_ready(tmp_path: Path, capsys) -> None:
    """A member whose activate-phase tasks are all persisted 'ok' gets an
    'already work-ready' feedback line, and no run is spawned."""
    from camp.provision.activation import activate_member

    group, _group_name, member_name, slug, _wt = _ready_member_with_tasks(
        tmp_path, work_state="ready", tasks_map={"dep-install": {"state": "ok"}}
    )
    env = _env(tmp_path)

    with patch("camp.provision.provision.spawn_detached_provisioner") as mock_spawn:
        activate_member(group, slug, member_name, env=env)

    mock_spawn.assert_not_called()
    captured = capsys.readouterr()
    assert "already work-ready" in captured.err.lower()


def test_feedback_retrying_previously_failed_work(tmp_path: Path, capsys) -> None:
    """A member whose last known work_state is 'failed' gets a feedback line
    naming the retry, distinguishable from a first-time 'queued' line, and a
    detached run IS spawned (failed work is retried, not left stuck)."""
    from camp.provision.activation import activate_member

    group, _group_name, member_name, slug, _wt = _ready_member_with_tasks(
        tmp_path, work_state="failed", tasks_map={"dep-install": {"state": "failed"}}
    )
    env = _env(tmp_path)

    with patch("camp.provision.provision.spawn_detached_provisioner") as mock_spawn:
        activate_member(group, slug, member_name, env=env)

    mock_spawn.assert_called_once()
    captured = capsys.readouterr()
    assert "failed" in captured.err.lower()
    assert "retry" in captured.err.lower() or "retrying" in captured.err.lower()
    assert "already work-ready" not in captured.err.lower()
    assert "already in progress" not in captured.err.lower()


def test_feedback_no_activate_task_declared(tmp_path: Path, capsys) -> None:
    """A member declaring no activate-phase task gets a feedback line saying
    so, rather than falsely claiming work was queued. Activation itself is
    unchanged: the member is still marked activated and the doc still prints."""
    from camp.provision.activation import activate_member
    from camp.group.manifest import read_central_manifest

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    mpath = _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
            }
        ],
    )
    group = _make_group(group_name, member_name, harness={"inject": "stdout"})  # no tasks
    env = _env(tmp_path)

    with patch("camp.provision.provision.spawn_detached_provisioner") as mock_spawn:
        activate_member(group, slug, member_name, env=env)

    mock_spawn.assert_not_called()
    captured = capsys.readouterr()
    assert "queued" not in captured.err.lower()
    assert "no activate-phase task" in captured.err.lower()

    data = read_central_manifest(mpath)
    member_entry = next(m for m in data["members"] if m["name"] == member_name)
    assert member_entry.get("activated") is True


# ---------------------------------------------------------------------------
# The crash case: a guard holder killed mid-run leaves the guard free.
# ---------------------------------------------------------------------------


def test_guard_released_when_holder_process_is_killed(tmp_path: Path) -> None:
    """A holder that dies without releasing leaves the guard free — the next
    activation runs the tasks rather than treating the member as already in
    progress. Kills a REAL process holding the flock (not a simulated flag)."""
    import camp.provision.activation as activation

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)
    ws_dir = wt_path.parent

    _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
                "activated": True,
            }
        ],
    )
    tasks = [_activate_task("dep-install", [["npm", "install"]])]
    group = _make_group(group_name, member_name, tasks=tasks)
    env = _env(tmp_path)

    lock_path = activation.member_guard_lock_path(ws_dir, member_name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    holder_src = (
        "import fcntl, time\n"
        f"fd = open({str(lock_path)!r}, 'w')\n"
        "fcntl.flock(fd.fileno(), fcntl.LOCK_EX)\n"
        "time.sleep(60)\n"
    )
    holder = subprocess.Popen([sys.executable, "-c", holder_src])
    try:
        # Wait for the holder to actually acquire the flock before killing it —
        # a race against "started but not yet locked" would prove nothing.
        deadline = time.monotonic() + 5.0
        acquired_and_released = False
        while time.monotonic() < deadline:
            probe_fd = open(str(lock_path), "w")
            try:
                fcntl.flock(probe_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(probe_fd.fileno(), fcntl.LOCK_UN)
                probe_fd.close()
                time.sleep(0.02)
                continue
            except OSError:
                probe_fd.close()
                acquired_and_released = True
                break
        assert acquired_and_released, "holder subprocess never acquired the flock"

        os.kill(holder.pid, signal.SIGKILL)
        holder.wait(timeout=5)
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=5)

    # The OS must have released the flock along with the killed process.
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        activation.run_activate_tasks_in_background(group, slug, member_name, env=env)

    mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Lockfile discipline: never unlinked without the lock held; inode re-checked
# on acquire.
# ---------------------------------------------------------------------------


def test_guard_reap_skips_lockfile_currently_held(tmp_path: Path) -> None:
    """reap_member_guard_unlocked never force-unlinks a lockfile whose guard
    is currently held — it is skipped, not removed out from under a live run."""
    import camp.provision.activation as activation

    ws_dir = tmp_path / "camp" / "mygroup" / "worktrees" / "my-slug"
    ws_dir.mkdir(parents=True, exist_ok=True)
    member_name = "myrepo"

    lock_path = activation.member_guard_lock_path(ws_dir, member_name)
    holder_fd = open(str(lock_path), "w")
    fcntl.flock(holder_fd.fileno(), fcntl.LOCK_EX)
    try:
        activation.reap_member_guard_unlocked(ws_dir, [member_name])
        assert lock_path.exists(), "a held lockfile must not be unlinked"
    finally:
        fcntl.flock(holder_fd.fileno(), fcntl.LOCK_UN)
        holder_fd.close()


def test_guard_reap_removes_lockfile_when_free(tmp_path: Path) -> None:
    """reap_member_guard_unlocked removes a lockfile whose guard is free."""
    import camp.provision.activation as activation

    ws_dir = tmp_path / "camp" / "mygroup" / "worktrees" / "my-slug"
    ws_dir.mkdir(parents=True, exist_ok=True)
    member_name = "myrepo"

    lock_path = activation.member_guard_lock_path(ws_dir, member_name)
    lock_path.write_text("")
    assert lock_path.exists()

    activation.reap_member_guard_unlocked(ws_dir, [member_name])
    assert not lock_path.exists()


def test_guard_acquire_rechecks_inode_and_fails_safe_on_race(
    tmp_path: Path, monkeypatch
) -> None:
    """An acquire re-checks the lockfile's inode after flock succeeds — if the
    path was unlinked and recreated in the window between open() and the
    check (simulated here), the acquire fails safe instead of granting a
    lock on a stale inode.

    The fake os.stat below intercepts ONLY the exact lock_path under test —
    every other path (pytest/import-machinery stats included) passes straight
    through to the real os.stat untouched, so this cannot clobber unrelated
    files the way patching os.stat unconditionally would.
    """
    import camp.provision.activation as activation

    ws_dir = tmp_path / "camp" / "mygroup" / "worktrees" / "my-slug"
    ws_dir.mkdir(parents=True, exist_ok=True)
    member_name = "myrepo"
    lock_path = activation.member_guard_lock_path(ws_dir, member_name)

    real_stat = os.stat

    def racing_stat(path, *a, **kw):
        if Path(path) == lock_path:
            # Simulate a concurrent reap unlinking + recreating THIS lockfile
            # in the window between this acquire's flock() and its inode
            # re-check — and nothing else.
            Path(path).unlink(missing_ok=True)
            Path(path).write_text("")
        return real_stat(path, *a, **kw)

    monkeypatch.setattr(activation.os, "stat", racing_stat)

    fd = activation._try_acquire_member_guard(ws_dir, member_name)
    assert fd is None, "acquire must fail safe when the inode changed under it"


# ---------------------------------------------------------------------------
# Teardown vs the activate-phase guard.
# ---------------------------------------------------------------------------


def _git_linked_worktree(tmp_path: Path, ws_dir: Path, member_name: str) -> tuple[Path, Path]:
    """Create a real upstream repo + a linked worktree at ws_dir/member_name.

    reconcile_break's removal path runs `git -C repo_root worktree remove
    wt_path` — repo_root and wt_path must therefore be the upstream repo and
    an actual LINKED worktree of it, not the same directory, or the remove
    fails and reconcile_break never reaches its post-removal reap step.
    Returns (repo_root, wt_path).
    """
    upstream = tmp_path / "upstream"
    upstream.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(upstream)], check=True)
    subprocess.run(
        ["git", "-C", str(upstream), "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    wt_path = ws_dir / member_name
    ws_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(upstream), "worktree", "add", "-q", str(wt_path)], check=True
    )
    return upstream, wt_path


def test_reconcile_break_reaps_member_guard_lockfiles_on_full_teardown(tmp_path: Path) -> None:
    """reconcile_break reaps a member's leaked activate-phase guard lockfile
    once the slug is fully torn down — without this, every removed workspace
    that ever ran activate-phase work leaks one lockfile per member forever."""
    import camp.provision.activation as activation
    from camp.provision.reconcile import reconcile_break

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    env = _env(tmp_path)

    ws_dir = tmp_path / "camp" / group_name / "worktrees" / slug
    repo_root, wt_path = _git_linked_worktree(tmp_path, ws_dir, member_name)

    _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": str(repo_root),
                "worktree_path": str(wt_path),
                "provision_state": "ready",
                "activated": True,
            }
        ],
    )

    # A leaked lockfile from a completed (and never-reaped, pre-this-slice)
    # activate run — guard currently free.
    lock_path = activation.member_guard_lock_path(ws_dir, member_name)
    lock_path.write_text("")
    assert lock_path.exists()

    group = {
        "group": {"name": group_name},
        "members": [{"name": member_name, "repo_root": str(repo_root)}],
        "branch_pattern": "worktree-{slug}",
    }

    result = reconcile_break(group, slug, env=env, force=True)

    assert result["status"] == "ok", result
    assert not lock_path.exists(), "member guard lockfile must be reaped on full teardown"


def test_reconcile_break_does_not_block_on_in_progress_activation(tmp_path: Path) -> None:
    """camp remove does not block for an in-flight activate run's full
    duration: the guard held by a long activate-phase run must not stall
    reconcile_break's teardown critical section."""
    import threading

    import camp.provision.activation as activation
    from camp.provision.reconcile import reconcile_break

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    env = _env(tmp_path)

    ws_dir = tmp_path / "camp" / group_name / "worktrees" / slug
    repo_root, wt_path = _git_linked_worktree(tmp_path, ws_dir, member_name)

    _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": str(repo_root),
                "worktree_path": str(wt_path),
                "provision_state": "ready",
                "activated": True,
            }
        ],
    )

    group = {
        "group": {"name": group_name},
        "members": [{"name": member_name, "repo_root": str(repo_root)}],
        "branch_pattern": "worktree-{slug}",
    }

    lock_path = activation.member_guard_lock_path(ws_dir, member_name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder_fd = open(str(lock_path), "w")
    fcntl.flock(holder_fd.fileno(), fcntl.LOCK_EX)

    release_event = threading.Event()

    def hold_guard():
        release_event.wait(5.0)
        fcntl.flock(holder_fd.fileno(), fcntl.LOCK_UN)
        holder_fd.close()

    holder_thread = threading.Thread(target=hold_guard)
    holder_thread.start()
    try:
        start = time.monotonic()
        result = reconcile_break(group, slug, env=env, force=True)
        elapsed = time.monotonic() - start
        assert elapsed < 3.0, (
            f"reconcile_break took {elapsed:.2f}s — it must not block on the "
            "in-progress activate-phase guard"
        )
        assert result["status"] == "ok", result
    finally:
        release_event.set()
        holder_thread.join(timeout=5.0)


# ---------------------------------------------------------------------------
# Unknown hook kind → GroupConfigError naming member + kind
# ---------------------------------------------------------------------------


def test_unknown_hook_kind_raises_group_config_error(tmp_path: Path) -> None:
    """Unknown hook kind in config raises GroupConfigError naming member + kind."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[members.hooks]]
kind = "not-a-known-kind"
cmd = ["npm", "install"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert "not-a-known-kind" in msg
    assert "myrepo" in msg


# ---------------------------------------------------------------------------
# group_config: activation hook block parsing and validation
# ---------------------------------------------------------------------------


def test_group_config_parses_activation_hooks(tmp_path: Path) -> None:
    """group_config.load_group normalizes [[members.hooks]] dep-install entries
    into one implicit 'dep-install' task, one step per hook, argv preserved."""
    from camp.group.config import load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[members.hooks]]
kind = "dep-install"
cmd = ["npm", "install"]

[[members.hooks]]
kind = "dep-install"
cmd = ["pip", "install", "-e", "."]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    cfg = load_group(f)
    tasks = cfg["members"][0]["tasks"]
    assert len(tasks) == 1
    dep_install_task = tasks[0]
    assert dep_install_task["name"] == "dep-install"
    assert dep_install_task["phase"] == "activate"
    assert dep_install_task["required"] is True
    steps = dep_install_task["steps"]
    assert len(steps) == 2
    assert steps[0]["cmd"] == ["npm", "install"]
    assert steps[1]["cmd"] == ["pip", "install", "-e", "."]


def test_group_config_no_hooks_defaults_to_empty_list(tmp_path: Path) -> None:
    """When no [[members.hooks]], no implicit dep-install task is created."""
    from camp.group.config import load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    cfg = load_group(f)
    assert cfg["members"][0]["tasks"] == []


def test_group_config_hook_cmd_must_be_list(tmp_path: Path) -> None:
    """hook.cmd as a string (not a list) → GroupConfigError."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[members.hooks]]
kind = "dep-install"
cmd = "npm install"
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert "cmd" in msg


def test_group_config_hook_cmd_elements_must_be_strings(tmp_path: Path) -> None:
    """hook.cmd containing a non-string element → GroupConfigError."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[members.hooks]]
kind = "dep-install"
cmd = ["npm", 42]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert "cmd" in msg


def test_group_config_hook_empty_token_rejected(tmp_path: Path) -> None:
    """An empty string token in hook.cmd is rejected (strip-and-reject guard)."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[members.hooks]]
kind = "dep-install"
cmd = ["npm", "", "install"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert "empty" in msg.lower() or "whitespace" in msg.lower() or "blank" in msg.lower()


def test_group_config_hook_whitespace_only_token_rejected(tmp_path: Path) -> None:
    """A whitespace-only string token in hook.cmd is rejected."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[members.hooks]]
kind = "dep-install"
cmd = ["npm", "   ", "install"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert "empty" in msg.lower() or "whitespace" in msg.lower() or "blank" in msg.lower()


def test_group_config_hook_missing_kind_errors(tmp_path: Path) -> None:
    """A hook missing 'kind' → GroupConfigError."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[members.hooks]]
cmd = ["npm", "install"]
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert "kind" in msg


def test_group_config_hook_missing_cmd_errors(tmp_path: Path) -> None:
    """A hook missing 'cmd' → GroupConfigError."""
    from camp.group.config import GroupConfigError, load_group

    toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[[members.hooks]]
kind = "dep-install"
"""
    f = tmp_path / "testgroup.toml"
    f.write_text(toml)
    with pytest.raises(GroupConfigError) as exc_info:
        load_group(f)
    msg = str(exc_info.value)
    assert "cmd" in msg


# ---------------------------------------------------------------------------
# GroupConfigError from the REAL CLI entrypoint (not just load_group).
#
# Regression: _resolve_group_for_command had a bare `except Exception: return
# (None, None)` that swallowed GroupConfigError.  A malformed config (unknown
# hook kind) caused `camp activate <member>` to fall through to spine and print
# an unrelated error instead of naming the member + kind.
# ---------------------------------------------------------------------------

_REPO_ROOT_FOR_CLI = Path(__file__).resolve().parents[3]
_CLI_CAMP = _REPO_ROOT_FOR_CLI / "tools" / "camp" / "plugins" / "camp" / "cli" / "camp"


def _run_cli(args: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess:
    base = {**os.environ}
    base.update(env)
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env=base,
    )


def test_cli_activate_unknown_hook_kind_exits_nonzero_with_legible_message(
    tmp_path: Path,
) -> None:
    """camp activate <member> against a config with an unknown hook kind must exit
    non-zero and name both the member and the unknown kind in the error output.

    Regression: _resolve_group_for_command swallowed GroupConfigError via a bare
    `except Exception`, causing this to fall through to an unrelated error.
    """
    # Write a config with an unknown hook kind.
    groups_dir = tmp_path / "groups"
    groups_dir.mkdir(parents=True)
    (groups_dir / "badgroup.toml").write_text(
        '[group]\nname = "badgroup"\n\n'
        '[[members]]\nname = "myrepo"\nrepo_root = "/tmp/fake-myrepo"\n\n'
        '[[members.hooks]]\nkind = "not-a-valid-kind"\ncmd = ["echo", "hi"]\n'
    )

    env = {
        "CAMP_CONFIG_DIR": str(tmp_path),
        "CAMP_STATE_DIR": str(tmp_path / "state"),
    }

    result = _run_cli(
        ["activate", "myrepo", "--group", "badgroup", "--name", "any-slug"],
        env=env,
    )

    assert result.returncode != 0, (
        "camp activate with an unknown hook kind must exit non-zero.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "not-a-valid-kind" in combined, (
        f"Error output must name the unknown hook kind.\ncombined: {combined}"
    )
    assert "myrepo" in combined, f"Error output must name the member.\ncombined: {combined}"


# ---------------------------------------------------------------------------
# Failing required activate-task, via the --background invocation — legible,
# no raw traceback, persisted for `camp status` to surface later.
# ---------------------------------------------------------------------------


def test_failing_required_task_surfaces_legibly_via_cli_background(tmp_path: Path) -> None:
    """camp activate <member> --background — the detached run's own invocation —
    must not dump a raw traceback when a required activate-task fails, and must
    persist the failure so `camp status` can surface it."""
    import json as _json

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"

    # Build state dir layout.
    state_dir = tmp_path / "state"
    wt_path = state_dir / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    manifest_dir = state_dir / group_name / "worktrees" / slug
    manifest_path = manifest_dir / "manifest.json"
    manifest_path.write_text(
        _json.dumps(
            {
                "schema_version": 1,
                "group": group_name,
                "slug": slug,
                "branch": f"worktree-{slug}",
                "members": [
                    {
                        "name": member_name,
                        "repo_root": "/tmp/fake-repo",
                        "worktree_path": str(wt_path),
                        "provision_state": "ready",
                        "activated": True,
                    }
                ],
            }
        )
    )

    # Write a config with a hook that will legitimately fail (cmd = ["false"]).
    groups_dir = tmp_path / "groups"
    groups_dir.mkdir(parents=True)
    (groups_dir / f"{group_name}.toml").write_text(
        f'[group]\nname = "{group_name}"\n\n'
        f'[[members]]\nname = "{member_name}"\nrepo_root = "/tmp/fake-repo"\n\n'
        f'[[members.hooks]]\nkind = "dep-install"\ncmd = ["false"]\n'
    )

    env = {
        "CAMP_CONFIG_DIR": str(tmp_path),
        "CAMP_STATE_DIR": str(state_dir),
    }

    result = _run_cli(
        ["activate", member_name, "--group", group_name, "--name", slug, "--background"],
        env=env,
    )

    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, (
        f"Must not dump a raw Python traceback. combined: {combined}"
    )

    from camp.group.manifest import read_central_manifest

    data = read_central_manifest(manifest_path)
    member_entry = next(m for m in data["members"] if m["name"] == member_name)
    assert member_entry["work_state"] == "failed"
    assert member_entry["tasks"]["dep-install"]["state"] == "failed"


# ---------------------------------------------------------------------------
# inject strategy dispatch in activate_member
# ---------------------------------------------------------------------------


def _ready_member_setup(tmp_path: Path, doc: str | None) -> tuple[str, str, str, Path]:
    """Build a ready member + manifest; optionally write its CLAUDE.md.

    Returns (group_name, member_name, slug, workspace_dir).
    """
    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    ws_dir = tmp_path / "camp" / group_name / "worktrees" / slug
    wt_path = ws_dir / member_name
    wt_path.mkdir(parents=True, exist_ok=True)
    if doc is not None:
        (wt_path / "CLAUDE.md").write_text(doc)

    _make_manifest(
        tmp_path,
        slug,
        group_name,
        [
            {
                "name": member_name,
                "repo_root": "/tmp/fake-repo",
                "worktree_path": str(wt_path),
                "provision_state": "ready",
            }
        ],
    )
    return group_name, member_name, slug, ws_dir


def test_activate_claude_hook_enqueues_doc_not_stdout(tmp_path: Path, capsys) -> None:
    """Under claude-hook WITH the drain hook installed, the full doc is enqueued,
    NOT dumped to stdout."""
    from camp.provision.activation import activate_member
    from camp.launch.inject import queue_dir_for
    from camp.launch.hooks_writer import write_workspace_inject_hook

    doc = "# Member CLAUDE.md\n\nFULL-DOC-BODY-marker\n"
    group_name, member_name, slug, ws_dir = _ready_member_setup(tmp_path, doc)

    # The drain hook must be present for the hook channel to be claimed.
    write_workspace_inject_hook(ws_dir, "/abs/camp")

    # No [harness] block → claude default → claude-hook strategy.
    group = _make_group(group_name, member_name)
    env = _env(tmp_path)

    activate_member(group, slug, member_name, env=env)

    # Full doc must be enqueued.
    files = list(queue_dir_for(ws_dir).iterdir())
    assert len(files) == 1
    assert doc in files[0].read_text()

    # Full doc must NOT be on stdout.
    captured = capsys.readouterr()
    assert "FULL-DOC-BODY-marker" not in captured.out


def test_activate_claude_hook_prints_concise_confirmation(tmp_path: Path, capsys) -> None:
    """Under claude-hook WITH the drain hook installed, a concise confirmation
    naming the member is printed to stdout."""
    from camp.provision.activation import activate_member
    from camp.launch.hooks_writer import write_workspace_inject_hook

    doc = "# Member doc\n"
    group_name, member_name, slug, ws_dir = _ready_member_setup(tmp_path, doc)

    write_workspace_inject_hook(ws_dir, "/abs/camp")

    group = _make_group(group_name, member_name)
    env = _env(tmp_path)

    activate_member(group, slug, member_name, env=env)

    captured = capsys.readouterr()
    assert member_name in captured.out
    # The confirmation should mention the inject hook channel.
    assert "next turn" in captured.out.lower() or "hook" in captured.out.lower()


def test_activate_claude_hook_without_drain_hook_falls_back_to_stdout(
    tmp_path: Path, capsys
) -> None:
    """claude-hook strategy but NO drain hook installed → fall back to printing the
    full doc to stdout; no false 'will load via hook' claim."""
    from camp.provision.activation import activate_member
    from camp.launch.inject import queue_dir_for

    doc = "# Member CLAUDE.md\n\nFULL-DOC-BODY-marker\n"
    group_name, member_name, slug, ws_dir = _ready_member_setup(tmp_path, doc)

    # No drain hook written to <workspace>/.claude/settings.json.
    group = _make_group(group_name, member_name)
    env = _env(tmp_path)

    activate_member(group, slug, member_name, env=env)

    captured = capsys.readouterr()
    # Content must still reach the agent — full doc on stdout.
    assert "FULL-DOC-BODY-marker" in captured.out
    # No false claim that it will load via the hook.
    assert "next turn" not in captured.out.lower()
    # Nothing relied on the (absent) drain — queue must not be the only delivery.
    qdir = queue_dir_for(ws_dir)
    assert not qdir.exists() or list(qdir.iterdir()) == []


def test_activate_stdout_strategy_prints_full_doc(tmp_path: Path, capsys) -> None:
    """Under the stdout strategy, the full doc is printed to stdout (unchanged)."""
    from camp.provision.activation import activate_member
    from camp.launch.inject import queue_dir_for

    doc = "# Member CLAUDE.md\n\nFULL-DOC-BODY-marker\n"
    group_name, member_name, slug, ws_dir = _ready_member_setup(tmp_path, doc)

    group = _make_group(group_name, member_name, harness={"inject": "stdout"})
    env = _env(tmp_path)

    activate_member(group, slug, member_name, env=env)

    captured = capsys.readouterr()
    assert "FULL-DOC-BODY-marker" in captured.out

    # Nothing enqueued under stdout strategy.
    qdir = queue_dir_for(ws_dir)
    assert not qdir.exists() or list(qdir.iterdir()) == []
