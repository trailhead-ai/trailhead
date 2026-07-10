"""Tests for activation.py — camp activate <member>.

Test contract:
- camp activate <ready-member>: runs each activate-phase task step once
  (list-mode, fake subprocess), prints the member's CLAUDE.md content, marks
  activated; re-activate → tasks NOT re-run, doc re-printed.
- camp activate <pending-member> → "still provisioning" message + retry hint,
  tasks NOT run.
- camp activate <failed-member> → names the failure + retry command.
- A legacy dep-install hook config still executes at first activate (via
  load_group's normalization into an implicit required activate-phase task).
- A required activate-task failure aborts (TaskError) and leaves activated unset.
- An optional activate-task failure marks activated=True and warns on stderr.
- malformed/unknown hook kind in config → GroupConfigError naming member + kind.
- group_config parses + validates the activation-hook block: string-list
  enforcement, PLUS strip-and-reject empty/whitespace-only argv tokens.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
# activate_member: ready member — runs task steps, prints CLAUDE.md, marks activated
# ---------------------------------------------------------------------------


def test_activate_ready_runs_each_step_once(tmp_path: Path) -> None:
    """A ready member: each activate-phase task step is run exactly once (list-mode)."""
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

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        activate_member(group, slug, member_name, env=env)

    assert mock_run.call_count == 2
    first_call_argv = mock_run.call_args_list[0][0][0]
    second_call_argv = mock_run.call_args_list[1][0][0]
    assert first_call_argv == ["npm", "install"]
    assert second_call_argv == ["pip", "install", "-e", "."]


def test_activate_ready_tasks_run_shell_false(tmp_path: Path) -> None:
    """Activate-phase task steps are run with shell=False (list-mode, trust)."""
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

    tasks = [_activate_task("dep-install", [["npm", "install"]])]
    group = _make_group(group_name, member_name, tasks=tasks)
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        activate_member(group, slug, member_name, env=env)

    kwargs = mock_run.call_args_list[0][1]
    assert kwargs.get("shell") is not True


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


def test_activate_ready_records_task_state_in_manifest(tmp_path: Path) -> None:
    """A successful activate-phase task records state 'ok' in the manifest tasks map."""
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

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        activate_member(group, slug, member_name, env=env)

    data = read_central_manifest(mpath)
    member_entry = next(m for m in data["members"] if m["name"] == member_name)
    assert member_entry["tasks"]["dep-install"]["state"] == "ok"


def test_activate_ready_reactivate_does_not_rerun_tasks(tmp_path: Path) -> None:
    """Re-activating an already-activated member skips tasks; doc is still printed."""
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
                "activated": True,  # already activated
            }
        ],
    )

    tasks = [_activate_task("dep-install", [["npm", "install"]])]
    group = _make_group(group_name, member_name, tasks=tasks, harness={"inject": "stdout"})
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        import io
        import contextlib

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            activate_member(group, slug, member_name, env=env)
        mock_run.assert_not_called()

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
# Legacy dep-install hook config still executes at first activate (normalized).
# ---------------------------------------------------------------------------


def test_legacy_dep_install_executes_at_first_activate(tmp_path: Path) -> None:
    """A legacy [[members.hooks]] dep-install block, normalized by load_group into
    an implicit required activate-phase task, still runs at first activate."""
    from camp.group.config import load_group
    from camp.provision.activation import activate_member
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
            }
        ],
    )
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        activate_member(group, slug, member_name, env=env)

    assert mock_run.call_count == 1
    assert mock_run.call_args_list[0][0][0] == ["echo", "installing"]

    data = read_central_manifest(mpath)
    member_entry = next(m for m in data["members"] if m["name"] == member_name)
    assert member_entry.get("activated") is True
    assert member_entry["tasks"]["dep-install"]["state"] == "ok"


# ---------------------------------------------------------------------------
# Required activate-task failure — aborts, activated stays UNSET.
# ---------------------------------------------------------------------------


def test_required_task_failure_does_not_mark_activated(tmp_path: Path) -> None:
    """When a required activate-task fails, TaskError propagates and activated is
    NOT set in the manifest."""
    from camp.provision.activation import activate_member
    from camp.provision.tasks import TaskError
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

    tasks = [_activate_task("dep-install", [["false"]], required=True)]
    group = _make_group(group_name, member_name, tasks=tasks)
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        with pytest.raises(TaskError):
            activate_member(group, slug, member_name, env=env)

    data = read_central_manifest(mpath)
    member_entry = next(m for m in data["members"] if m["name"] == member_name)
    assert not member_entry.get("activated", False), (
        "activated must NOT be set when a required activate-task fails"
    )


# ---------------------------------------------------------------------------
# Optional activate-task failure — marks activated, warns on stderr, proceeds.
# ---------------------------------------------------------------------------


def test_optional_task_failure_marks_activated_and_warns(tmp_path: Path, capsys) -> None:
    """An optional activate-task failure warns on stderr, records the failed state,
    and activation PROCEEDS (member is marked activated)."""
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

    tasks = [_activate_task("optional-task", [["false"]], required=False)]
    group = _make_group(group_name, member_name, tasks=tasks, harness={"inject": "stdout"})
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="nope")
        activate_member(group, slug, member_name, env=env)

    data = read_central_manifest(mpath)
    member_entry = next(m for m in data["members"] if m["name"] == member_name)
    assert member_entry.get("activated") is True
    assert member_entry["tasks"]["optional-task"]["state"] == "failed"

    captured = capsys.readouterr()
    assert "optional-task" in captured.err
    assert member_name in captured.err
    assert "camp status" in captured.err


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
# Failing required activate-task — legible error via CLI, no raw traceback.
# ---------------------------------------------------------------------------


def test_failing_required_task_surfaces_legibly_via_cli(tmp_path: Path) -> None:
    """camp activate <member> when a required activate-task fails must exit
    non-zero and name the member in the error; no raw Python traceback."""
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
        ["activate", member_name, "--group", group_name, "--name", slug],
        env=env,
    )

    assert result.returncode != 0, (
        "camp activate with a failing required task must exit non-zero.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    # Must name the member; must NOT be a raw traceback.
    assert member_name in combined or "task" in combined.lower(), (
        f"Error must reference the member or task. combined: {combined}"
    )
    assert "Traceback" not in combined, (
        f"Must not dump a raw Python traceback. combined: {combined}"
    )


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
    from camp.harness.inject import queue_dir_for
    from camp.harness.hooks_writer import write_workspace_inject_hook

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
    from camp.harness.hooks_writer import write_workspace_inject_hook

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
    from camp.harness.inject import queue_dir_for

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
    from camp.harness.inject import queue_dir_for

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
