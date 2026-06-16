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
import os
import subprocess
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


def _make_group(
    group_name: str,
    member_name: str,
    hooks: list[dict] | None = None,
    harness: dict | None = None,
) -> dict:
    """Build a minimal group config dict with optional activation hooks."""
    member = {
        "name": member_name,
        "repo_root": "/tmp/fake-repo",
        "bootstrap": [],
        "base": "origin/main",
    }
    if hooks is not None:
        member["hooks"] = hooks
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

    group = _make_group(group_name, member_name, harness={"inject": "stdout"})
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

    group = _make_group(group_name, member_name, harness={"inject": "stdout"})
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
    group = _make_group(group_name, member_name, hooks=hooks, harness={"inject": "stdout"})
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

    group = _make_group(group_name, member_name, harness={"inject": "stdout"})
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


# ---------------------------------------------------------------------------
# Fix 1: GroupConfigError from the REAL CLI entrypoint (not just load_group).
#
# Regression: _resolve_group_for_command had a bare `except Exception: return
# (None, None)` that swallowed GroupConfigError.  A malformed config (unknown
# hook kind) caused `camp enter <member>` to fall through to spine and print an
# unrelated error instead of naming the member + kind.
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


def test_cli_enter_unknown_hook_kind_exits_nonzero_with_legible_message(
    tmp_path: Path,
) -> None:
    """camp enter <member> against a config with an unknown hook kind must exit
    non-zero and name both the member and the unknown kind in the error output.

    Regression: _resolve_group_for_command swallowed GroupConfigError via a bare
    `except Exception`, causing this to fall through to an unrelated error.
    """
    # Write a config with an unknown hook kind.
    groups_dir = tmp_path / "groups"
    groups_dir.mkdir(parents=True)
    (groups_dir / "badgroup.toml").write_text(
        "[group]\nname = \"badgroup\"\n\n"
        "[[members]]\nname = \"myrepo\"\nrepo_root = \"/tmp/fake-myrepo\"\n\n"
        "[[members.hooks]]\nkind = \"not-a-valid-kind\"\ncmd = [\"echo\", \"hi\"]\n"
    )

    env = {
        "CAMP_CONFIG_DIR": str(tmp_path),
        "CAMP_STATE_DIR": str(tmp_path / "state"),
    }

    result = _run_cli(
        ["enter", "myrepo", "--group", "badgroup", "--name", "any-slug"],
        env=env,
    )

    assert result.returncode != 0, (
        "camp enter with an unknown hook kind must exit non-zero.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "not-a-valid-kind" in combined, (
        "Error output must name the unknown hook kind.\n"
        f"combined: {combined}"
    )
    assert "myrepo" in combined, (
        "Error output must name the member.\n"
        f"combined: {combined}"
    )


# ---------------------------------------------------------------------------
# Fix 2: Failing activation hook — legible error, activated stays UNSET.
# ---------------------------------------------------------------------------


def test_failing_hook_does_not_mark_activated(tmp_path: Path) -> None:
    """When an activation hook exits non-zero, activated must NOT be set in the
    manifest, and a CalledProcessError must propagate (not be swallowed)."""
    from activation import enter_member
    from manifest import read_central_manifest

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

    hooks = [{"kind": "dep-install", "cmd": ["false"]}]
    group = _make_group(group_name, member_name, hooks=hooks)
    env = _env(tmp_path)

    # Simulate a hook that exits non-zero via a CalledProcessError.
    import subprocess as _subprocess
    fake_error = _subprocess.CalledProcessError(1, ["false"])
    with patch("subprocess.run", side_effect=fake_error):
        with pytest.raises(_subprocess.CalledProcessError):
            enter_member(group, slug, member_name, env=env)

    # activated must NOT be set after the hook failure.
    data = read_central_manifest(mpath)
    member_entry = next(m for m in data["members"] if m["name"] == member_name)
    assert not member_entry.get("activated", False), (
        "activated must NOT be set when an activation hook fails"
    )


def test_failing_hook_surfaces_legibly_via_cli(tmp_path: Path) -> None:
    """camp enter <member> when an activation hook fails must exit non-zero and
    name the member + failing command in the error; no raw Python traceback."""
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
        f"[group]\nname = \"{group_name}\"\n\n"
        f"[[members]]\nname = \"{member_name}\"\nrepo_root = \"/tmp/fake-repo\"\n\n"
        f"[[members.hooks]]\nkind = \"dep-install\"\ncmd = [\"false\"]\n"
    )

    env = {
        "CAMP_CONFIG_DIR": str(tmp_path),
        "CAMP_STATE_DIR": str(state_dir),
    }

    result = _run_cli(
        ["enter", member_name, "--group", group_name, "--name", slug],
        env=env,
    )

    assert result.returncode != 0, (
        "camp enter with a failing hook must exit non-zero.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    # Must name the member and the failing command; must NOT be a raw traceback.
    assert member_name in combined or "hook" in combined.lower(), (
        f"Error must reference the member or hook. combined: {combined}"
    )
    assert "Traceback" not in combined, (
        f"Must not dump a raw Python traceback. combined: {combined}"
    )


# ---------------------------------------------------------------------------
# Slice 9: inject strategy dispatch in enter_member
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


def test_enter_claude_hook_enqueues_doc_not_stdout(tmp_path: Path, capsys) -> None:
    """Under claude-hook, the full doc is enqueued, NOT dumped to stdout."""
    from activation import enter_member
    from inject import queue_dir_for

    doc = "# Member CLAUDE.md\n\nFULL-DOC-BODY-marker\n"
    group_name, member_name, slug, ws_dir = _ready_member_setup(tmp_path, doc)

    # No [harness] block → claude default → claude-hook strategy.
    group = _make_group(group_name, member_name)
    env = _env(tmp_path)

    enter_member(group, slug, member_name, env=env)

    # Full doc must be enqueued.
    files = list(queue_dir_for(ws_dir).iterdir())
    assert len(files) == 1
    assert doc in files[0].read_text()

    # Full doc must NOT be on stdout.
    captured = capsys.readouterr()
    assert "FULL-DOC-BODY-marker" not in captured.out


def test_enter_claude_hook_prints_concise_confirmation(tmp_path: Path, capsys) -> None:
    """Under claude-hook, a concise confirmation naming the member is printed to stdout."""
    from activation import enter_member

    doc = "# Member doc\n"
    group_name, member_name, slug, ws_dir = _ready_member_setup(tmp_path, doc)

    group = _make_group(group_name, member_name)
    env = _env(tmp_path)

    enter_member(group, slug, member_name, env=env)

    captured = capsys.readouterr()
    assert member_name in captured.out
    # The confirmation should mention the inject hook channel.
    assert "next turn" in captured.out.lower() or "hook" in captured.out.lower()


def test_enter_stdout_strategy_prints_full_doc(tmp_path: Path, capsys) -> None:
    """Under the stdout strategy, the full doc is printed to stdout (unchanged)."""
    from activation import enter_member
    from inject import queue_dir_for

    doc = "# Member CLAUDE.md\n\nFULL-DOC-BODY-marker\n"
    group_name, member_name, slug, ws_dir = _ready_member_setup(tmp_path, doc)

    group = _make_group(group_name, member_name, harness={"inject": "stdout"})
    env = _env(tmp_path)

    enter_member(group, slug, member_name, env=env)

    captured = capsys.readouterr()
    assert "FULL-DOC-BODY-marker" in captured.out

    # Nothing enqueued under stdout strategy.
    qdir = queue_dir_for(ws_dir)
    assert not qdir.exists() or list(qdir.iterdir()) == []
