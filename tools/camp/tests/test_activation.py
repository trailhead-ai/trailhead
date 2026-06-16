"""Tests for activation.py — camp enter <member> (Slice 5).

Test contract:
- camp enter <ready-member>: fires each hook once (list-mode, fake subprocess),
  prints the member's CLAUDE.md content, marks activated; re-enter → hooks NOT
  re-run, doc re-printed.
- camp enter <pending-member> → "still provisioning" message + retry hint,
  hooks NOT run.
- camp enter <failed-member> → names the failure + retry command.
- malformed/unknown hook kind in config → GroupConfigError naming member + kind.
- group_config parses + validates the activation-hook block: string-list
  enforcement, PLUS strip-and-reject empty/whitespace-only argv tokens.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


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


def _make_group(group_name: str, member_name: str, hooks: list[dict] | None = None) -> dict:
    """Build a minimal group config dict with optional activation hooks."""
    member = {
        "name": member_name,
        "repo_root": "/tmp/fake-repo",
        "bootstrap": [],
        "base": "origin/main",
    }
    if hooks is not None:
        member["hooks"] = hooks
    return {
        "group": {"name": group_name},
        "members": [member],
        "branch_pattern": "worktree-{slug}",
        "shared_vaults": [],
    }


# ---------------------------------------------------------------------------
# enter_member: pending member → "still provisioning" + hint, no hooks
# ---------------------------------------------------------------------------


def test_enter_pending_prints_provisioning_message(tmp_path: Path) -> None:
    """A pending member → 'still provisioning' message + retry hint; hooks NOT run."""
    from activation import enter_member, MemberNotReadyError
    from group_config import GroupConfigError

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
        enter_member(group, slug, member_name, env=env)

    msg = str(exc_info.value)
    assert "still provisioning" in msg.lower() or "provisioning" in msg.lower()
    assert "camp status" in msg or "camp setup" in msg


def test_enter_pending_does_not_run_hooks(tmp_path: Path) -> None:
    """A pending member triggers MemberNotReadyError before any hook is fired."""
    from activation import enter_member, MemberNotReadyError

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
        hooks=[{"kind": "dep-install", "cmd": ["echo", "hook-ran"]}],
    )
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        with pytest.raises(MemberNotReadyError):
            enter_member(group, slug, member_name, env=env)
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# enter_member: failed member → names failure + retry command
# ---------------------------------------------------------------------------


def test_enter_failed_names_failure_and_retry(tmp_path: Path) -> None:
    """A failed member → MemberNotReadyError naming the failure and retry command."""
    from activation import enter_member, MemberNotReadyError

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
        enter_member(group, slug, member_name, env=env)

    msg = str(exc_info.value)
    assert failure_reason in msg
    assert "camp setup" in msg or "retry" in msg.lower()


# ---------------------------------------------------------------------------
# enter_member: ready member — fires hooks, prints CLAUDE.md, marks activated
# ---------------------------------------------------------------------------


def test_enter_ready_fires_each_hook_once(tmp_path: Path) -> None:
    """A ready member: each activation hook is fired exactly once (list-mode)."""
    from activation import enter_member

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

    hooks = [
        {"kind": "dep-install", "cmd": ["npm", "install"]},
        {"kind": "dep-install", "cmd": ["pip", "install", "-e", "."]},
    ]
    group = _make_group(group_name, member_name, hooks=hooks)
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = enter_member(group, slug, member_name, env=env)

    assert mock_run.call_count == 2
    first_call_argv = mock_run.call_args_list[0][0][0]
    second_call_argv = mock_run.call_args_list[1][0][0]
    assert first_call_argv == ["npm", "install"]
    assert second_call_argv == ["pip", "install", "-e", "."]


def test_enter_ready_hooks_run_shell_false(tmp_path: Path) -> None:
    """Activation hooks are run with shell=False (list-mode, D-F trust)."""
    from activation import enter_member

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

    hooks = [{"kind": "dep-install", "cmd": ["npm", "install"]}]
    group = _make_group(group_name, member_name, hooks=hooks)
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        enter_member(group, slug, member_name, env=env)

    kwargs = mock_run.call_args_list[0][1]
    assert kwargs.get("shell") is not True


def test_enter_ready_prints_member_claude_md(tmp_path: Path, capsys) -> None:
    """enter_member prints the member's CLAUDE.md content to stdout."""
    from activation import enter_member

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

    group = _make_group(group_name, member_name)
    env = _env(tmp_path)

    enter_member(group, slug, member_name, env=env)

    captured = capsys.readouterr()
    assert claude_md_content in captured.out


def test_enter_ready_prints_fallback_when_no_claude_md(tmp_path: Path, capsys) -> None:
    """When no CLAUDE.md exists, enter_member still prints something useful to stdout."""
    from activation import enter_member

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

    group = _make_group(group_name, member_name)
    env = _env(tmp_path)

    enter_member(group, slug, member_name, env=env)

    captured = capsys.readouterr()
    # Should mention the member name at minimum
    assert member_name in captured.out or member_name in captured.err


def test_enter_ready_marks_activated_in_manifest(tmp_path: Path) -> None:
    """After enter_member succeeds, the manifest member has activated=true."""
    from activation import enter_member
    from manifest import read_central_manifest, manifest_path_for

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
        mock_run.return_value = MagicMock(returncode=0)
        enter_member(group, slug, member_name, env=env)

    data = read_central_manifest(mpath)
    member_entry = next(m for m in data["members"] if m["name"] == member_name)
    assert member_entry.get("activated") is True


def test_enter_ready_reenter_does_not_rerun_hooks(tmp_path: Path) -> None:
    """Re-entering an already-activated member skips hooks; doc is still printed."""
    from activation import enter_member
    from manifest import read_central_manifest

    group_name = "mygroup"
    member_name = "myrepo"
    slug = "my-slug"
    wt_path = tmp_path / "camp" / group_name / "worktrees" / slug / member_name
    wt_path.mkdir(parents=True, exist_ok=True)

    claude_md_content = "# Member Doc\n"
    (wt_path / "CLAUDE.md").write_text(claude_md_content)

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
                "activated": True,  # already activated
            }
        ],
    )

    hooks = [{"kind": "dep-install", "cmd": ["npm", "install"]}]
    group = _make_group(group_name, member_name, hooks=hooks)
    env = _env(tmp_path)

    with patch("subprocess.run") as mock_run:
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            enter_member(group, slug, member_name, env=env)
        mock_run.assert_not_called()

    # Doc was still printed
    assert claude_md_content in out.getvalue()


def test_enter_ready_reenter_reprints_doc(tmp_path: Path, capsys) -> None:
    """Re-entering an activated member still prints the CLAUDE.md to stdout."""
    from activation import enter_member

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

    group = _make_group(group_name, member_name)
    env = _env(tmp_path)

    enter_member(group, slug, member_name, env=env)

    captured = capsys.readouterr()
    assert claude_md_content in captured.out


# ---------------------------------------------------------------------------
# Unknown hook kind → GroupConfigError naming member + kind
# ---------------------------------------------------------------------------


def test_unknown_hook_kind_raises_group_config_error(tmp_path: Path) -> None:
    """Unknown hook kind in config raises GroupConfigError naming member + kind."""
    from group_config import GroupConfigError, load_group

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
    """group_config.load_group parses [[members.hooks]] into each member dict."""
    from group_config import load_group

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
    hooks = cfg["members"][0]["hooks"]
    assert len(hooks) == 2
    assert hooks[0] == {"kind": "dep-install", "cmd": ["npm", "install"]}
    assert hooks[1] == {"kind": "dep-install", "cmd": ["pip", "install", "-e", "."]}


def test_group_config_no_hooks_defaults_to_empty_list(tmp_path: Path) -> None:
    """When no [[members.hooks]], member['hooks'] defaults to []."""
    from group_config import load_group

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
    assert cfg["members"][0]["hooks"] == []


def test_group_config_hook_cmd_must_be_list(tmp_path: Path) -> None:
    """hook.cmd as a string (not a list) → GroupConfigError."""
    from group_config import GroupConfigError, load_group

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
    from group_config import GroupConfigError, load_group

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
    from group_config import GroupConfigError, load_group

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
    from group_config import GroupConfigError, load_group

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
    from group_config import GroupConfigError, load_group

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
    from group_config import GroupConfigError, load_group

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
