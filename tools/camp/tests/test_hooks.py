"""Tests for camp init + SessionStart hook wiring.

The WorktreeRemove hook wiring is dropped (camp owns teardown via `camp rm`);
only the SessionStart hook is written into each member's .claude/settings.json.
The `worktree-cleanup` handler stays invocable but is not auto-wired.
Member worktrees now live under the unified workspace layout
central_state_dir(group)/worktrees/<slug>/<member>.

Test contract (all must RED before implementation, GREEN after):

1. init on a fake group writes the SessionStart hook entry (and NOT a
   WorktreeRemove entry) + the env.CAMP_BIN block into each member's
   .claude/settings.json.

2. Re-running init produces NO duplicate hook entries — idempotent.

3. An existing settings.json with unrelated keys is preserved (round-trip safety).

4. json.dumps robustness: a member repo_root / CAMP_BIN path containing a space
   and a quote is written + read back correctly (no broken JSON, no shell breakage).

5. session-bootstrap is idempotent (second run a no-op).

6. Silent exit-0 in all four no-op cases:
   a. cold-start: groups config dir absent entirely → exit 0, empty stderr.
   b. config-present-but-not-a-member → exit 0, empty stderr.
   c. malformed group config → exit 0, empty stderr.
   d. slug=None (cwd is a repo root, not a worktree) → exit 0, empty stderr.

7. Import guard under a bare python3: running session-bootstrap from a context where
   trailhead isn't already importable resolves via _bootstrap's walk-up (exit 0)
   OR emits the legible tier-4 error — never a raw ModuleNotFoundError traceback.

8. worktree-cleanup removes member worktrees + central manifest for a configured
   group.

9. A dirty worktree blocks cleanup unless --force; non-member cwd → silent no-op.

10. The hook command string shape: exactly "${CAMP_BIN:-<abs>} session-bootstrap"
    (shell-expandable form).

Fixtures use synthetic git repos + tmp .claude/settings.json (tmp_path).
The resolver's env= injection is used for all state paths.
Do NOT run camp init against the real ~/code/trailhead repo.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_BIN_CAMP = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


# ---------------------------------------------------------------------------
# Helpers shared with slice2 tests
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    """Initialize a real git repo at path with an initial commit."""
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
    readme = path / "README.md"
    readme.write_text("# test\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init", "--no-gpg-sign"],
        check=True,
        capture_output=True,
    )


def _make_group_config(
    name: str,
    members: list[dict[str, Any]],
    *,
    branch_pattern: str = "worktree-{slug}",
) -> dict[str, Any]:
    """Build a parsed group config dict matching group_config.load_group output."""
    return {
        "group": {"name": name},
        "members": members,
        "branch_pattern": branch_pattern,
    }


def _camp_state_env(tmp_path: Path) -> dict[str, str]:
    """Return env override dict pointing CAMP_STATE_DIR at tmp_path."""
    state_root = tmp_path / "camp-state"
    state_root.mkdir(parents=True, exist_ok=True)
    return {"CAMP_STATE_DIR": str(state_root)}


def _member_wt(group_name: str, slug: str, member: str, env: dict[str, str]) -> Path:
    """Return the unified-layout worktree path:
    central_state_dir(group)/worktrees/<slug>/<member>."""
    from camp.group.resolve import central_state_dir

    return central_state_dir(group_name, env=env) / "worktrees" / slug / member


# ---------------------------------------------------------------------------
# Fixture: 2-member synthetic group with real git repos
# ---------------------------------------------------------------------------


@pytest.fixture()
def two_member_group(tmp_path: Path):
    """A 2-member group with real git repos and env."""
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    _init_git_repo(repo_a)
    _init_git_repo(repo_b)

    group = _make_group_config(
        "testgroup",
        [
            {"name": "repo_a", "repo_root": str(repo_a), "bootstrap": []},
            {"name": "repo_b", "repo_root": str(repo_b), "bootstrap": []},
        ],
    )
    env = _camp_state_env(tmp_path)
    return {
        "group": group,
        "repo_a": repo_a,
        "repo_b": repo_b,
        "env": env,
        "tmp_path": tmp_path,
    }


# ---------------------------------------------------------------------------
# Test 1: init writes hook entries + env.CAMP_BIN into each member's settings.json
# ---------------------------------------------------------------------------


class TestHooksWriter:
    def test_writes_session_start_hook(self, two_member_group):
        """init writes a SessionStart hook for session-bootstrap into each
        member's settings.json."""
        from camp.launch.hooks_writer import write_hooks_for_member

        g = two_member_group
        repo_a = g["repo_a"]
        camp_bin = str(_BIN_CAMP)

        write_hooks_for_member(repo_a, camp_bin)

        settings_path = repo_a / ".claude" / "settings.json"
        assert settings_path.is_file()
        data = json.loads(settings_path.read_text())

        hooks = data.get("hooks", {})
        ss_hooks = hooks.get("SessionStart", [])
        assert len(ss_hooks) >= 1

        # Find a hook entry with session-bootstrap
        commands = [h.get("command", "") for entry in ss_hooks for h in entry.get("hooks", [])]
        assert any("session-bootstrap" in cmd for cmd in commands)

    def test_does_not_write_worktree_remove_hook(self, two_member_group):
        """WorktreeRemove wiring is dropped — camp owns teardown via `camp rm`.

        No WorktreeRemove entry must be written into the member's settings.json.
        """
        from camp.launch.hooks_writer import write_hooks_for_member

        g = two_member_group
        repo_a = g["repo_a"]
        camp_bin = str(_BIN_CAMP)

        write_hooks_for_member(repo_a, camp_bin)

        settings_path = repo_a / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text())

        hooks = data.get("hooks", {})
        assert "WorktreeRemove" not in hooks, (
            f"WorktreeRemove wiring should be dropped, got: {hooks!r}"
        )
        # The SessionStart wiring is retained.
        assert "SessionStart" in hooks

    def test_writes_env_camp_bin(self, two_member_group):
        """init writes the env.CAMP_BIN key into the settings.json."""
        from camp.launch.hooks_writer import write_hooks_for_member

        g = two_member_group
        repo_a = g["repo_a"]
        camp_bin = str(_BIN_CAMP)

        write_hooks_for_member(repo_a, camp_bin)

        settings_path = repo_a / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text())

        assert "env" in data
        assert data["env"].get("CAMP_BIN") == camp_bin

    def test_hook_command_shape(self, two_member_group):
        """The hook command string is exactly '${CAMP_BIN:-<abs>} session-bootstrap'."""
        from camp.launch.hooks_writer import write_hooks_for_member

        g = two_member_group
        repo_a = g["repo_a"]
        camp_bin = str(_BIN_CAMP)

        write_hooks_for_member(repo_a, camp_bin)

        settings_path = repo_a / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text())

        hooks = data.get("hooks", {})
        ss_hooks = hooks.get("SessionStart", [])
        commands = [h.get("command", "") for entry in ss_hooks for h in entry.get("hooks", [])]
        expected_cmd = f"${{CAMP_BIN:-{camp_bin}}} session-bootstrap"
        assert expected_cmd in commands, (
            f"Expected command {expected_cmd!r} not found in {commands}"
        )

    def test_idempotent_no_duplicates(self, two_member_group):
        """Re-running write_hooks_for_member adds NO duplicate hook entries."""
        from camp.launch.hooks_writer import write_hooks_for_member

        g = two_member_group
        repo_a = g["repo_a"]
        camp_bin = str(_BIN_CAMP)

        # Run twice
        write_hooks_for_member(repo_a, camp_bin)
        write_hooks_for_member(repo_a, camp_bin)

        settings_path = repo_a / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text())

        hooks = data.get("hooks", {})
        ss_hooks = hooks.get("SessionStart", [])

        ss_commands = [h.get("command", "") for entry in ss_hooks for h in entry.get("hooks", [])]

        expected_ss = f"${{CAMP_BIN:-{camp_bin}}} session-bootstrap"

        assert ss_commands.count(expected_ss) == 1, (
            f"Duplicate session-bootstrap entries: {ss_commands}"
        )
        # WorktreeRemove wiring is dropped.
        assert "WorktreeRemove" not in hooks

    def test_preserves_existing_unrelated_keys(self, two_member_group):
        """An existing settings.json with unrelated keys is preserved after write."""
        from camp.launch.hooks_writer import write_hooks_for_member

        g = two_member_group
        repo_a = g["repo_a"]
        camp_bin = str(_BIN_CAMP)

        # Pre-populate settings.json with unrelated content
        claude_dir = repo_a / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_path = claude_dir / "settings.json"
        existing = {
            "model": "claude-opus-4",
            "permissions": {"allow": ["Bash(git *)"]},
            "hooks": {
                "PreToolUse": [{"hooks": [{"type": "command", "command": "/some/other/hook.sh"}]}]
            },
        }
        settings_path.write_text(json.dumps(existing))

        write_hooks_for_member(repo_a, camp_bin)

        data = json.loads(settings_path.read_text())

        # Unrelated keys preserved
        assert data.get("model") == "claude-opus-4"
        assert data.get("permissions") == {"allow": ["Bash(git *)"]}

        # Pre-existing PreToolUse hook preserved
        pre_tool_use = data.get("hooks", {}).get("PreToolUse", [])
        assert any(
            h.get("command") == "/some/other/hook.sh"
            for entry in pre_tool_use
            for h in entry.get("hooks", [])
        ), "Pre-existing PreToolUse hook was dropped"

    def test_json_robustness_path_with_space_and_quote(self, tmp_path: Path):
        """A repo_root / CAMP_BIN path with a space and a quote round-trips correctly."""
        from camp.launch.hooks_writer import write_hooks_for_member

        # Create a repo_root path with a space and a quote in it
        spaced_dir = tmp_path / "my repo's dir"
        spaced_dir.mkdir(parents=True, exist_ok=True)
        _init_git_repo(spaced_dir)

        # camp_bin path with a space and a quote
        camp_bin_spaced = str(tmp_path / "my bin's/camp")

        write_hooks_for_member(spaced_dir, camp_bin_spaced)

        settings_path = spaced_dir / ".claude" / "settings.json"
        assert settings_path.is_file()

        # Must be valid JSON (not broken by the special chars)
        data = json.loads(settings_path.read_text())

        assert data["env"]["CAMP_BIN"] == camp_bin_spaced

        # The command string must contain the verbatim camp_bin_spaced
        ss_hooks = data.get("hooks", {}).get("SessionStart", [])
        commands = [h.get("command", "") for entry in ss_hooks for h in entry.get("hooks", [])]
        assert any(camp_bin_spaced in cmd for cmd in commands), (
            f"Expected camp_bin path in commands: {commands}"
        )


# ---------------------------------------------------------------------------
# Test 2: init_cmd writes hooks for all members in the group
# ---------------------------------------------------------------------------


class TestInitCmd:
    def test_init_writes_hooks_for_all_members(self, two_member_group):
        """camp init <group> writes hook entries to each member's .claude/settings.json."""
        from camp.workspace.init import run_init

        g = two_member_group
        camp_bin = str(_BIN_CAMP)

        run_init(g["group"], camp_bin)

        for repo_key in ("repo_a", "repo_b"):
            settings_path = g[repo_key] / ".claude" / "settings.json"
            assert settings_path.is_file(), f"settings.json missing for {repo_key}"
            data = json.loads(settings_path.read_text())
            assert "hooks" in data
            assert "SessionStart" in data["hooks"]
            assert "WorktreeRemove" not in data["hooks"]

    def test_init_idempotent(self, two_member_group):
        """Re-running init produces no duplicate hook entries for any member."""
        from camp.workspace.init import run_init

        g = two_member_group
        camp_bin = str(_BIN_CAMP)

        run_init(g["group"], camp_bin)
        run_init(g["group"], camp_bin)

        for repo_key in ("repo_a", "repo_b"):
            settings_path = g[repo_key] / ".claude" / "settings.json"
            data = json.loads(settings_path.read_text())

            ss_hooks = data.get("hooks", {}).get("SessionStart", [])
            commands = [h.get("command", "") for entry in ss_hooks for h in entry.get("hooks", [])]
            expected_cmd = f"${{CAMP_BIN:-{camp_bin}}} session-bootstrap"
            assert commands.count(expected_cmd) == 1, (
                f"Duplicate session-bootstrap entries in {repo_key}: {commands}"
            )


# ---------------------------------------------------------------------------
# Test 3: session-bootstrap handler — silent exit-0 no-op cases
# ---------------------------------------------------------------------------


def _run_session_bootstrap(
    cwd: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run camp session-bootstrap via the bin/camp wrapper."""
    env = {**os.environ}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(_BIN_CAMP), "session-bootstrap"],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


class TestSessionBootstrapNoOp:
    def test_cold_start_no_config_dir_exits_0_no_stderr(self, tmp_path: Path):
        """Cold start: groups config dir absent entirely → exit 0, no stderr."""
        # Use a CAMP_STATE_DIR and CAMP_CONFIG_DIR that don't exist at all
        nonexistent = str(tmp_path / "nonexistent")
        result = _run_session_bootstrap(
            cwd=str(tmp_path),
            extra_env={
                "CAMP_STATE_DIR": nonexistent,
                "CAMP_CONFIG_DIR": nonexistent,
            },
        )
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}: {result.stderr}"
        assert result.stderr == "", f"Expected empty stderr, got: {result.stderr!r}"

    def test_config_present_but_not_member_exits_0_no_stderr(self, tmp_path: Path):
        """config dir present but cwd repo not a member → exit 0, no stderr."""
        # CAMP_CONFIG_DIR override: config_dir("camp") returns the override directly.
        # So groups/ is at <CAMP_CONFIG_DIR>/groups (not <CAMP_CONFIG_DIR>/camp/groups).
        camp_config_dir = tmp_path / "camp-config"
        groups_dir = camp_config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)

        # Create a different repo for the group (not cwd)
        other_repo = tmp_path / "other_repo"
        _init_git_repo(other_repo)

        toml_content = f"""
[group]
name = "somegroup"

[[members]]
name = "other"
repo_root = "{other_repo!s}"
bootstrap = []
"""
        (groups_dir / "somegroup.toml").write_text(toml_content)

        # cwd = a completely different repo, not in any group
        unrelated_repo = tmp_path / "unrelated"
        unrelated_repo.mkdir(parents=True, exist_ok=True)

        result = _run_session_bootstrap(
            cwd=str(unrelated_repo),
            extra_env={
                "CAMP_STATE_DIR": str(tmp_path / "state"),
                "CAMP_CONFIG_DIR": str(camp_config_dir),
            },
        )
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}: {result.stderr}"
        assert result.stderr == "", f"Expected empty stderr, got: {result.stderr!r}"

    def test_malformed_config_exits_0_no_stderr(self, tmp_path: Path):
        """Malformed group config → exit 0, no stderr noise at session start."""
        camp_config_dir = tmp_path / "camp-config"
        groups_dir = camp_config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)

        # Write a malformed TOML
        (groups_dir / "bad.toml").write_text("this is not [valid toml\n")

        result = _run_session_bootstrap(
            cwd=str(tmp_path),
            extra_env={
                "CAMP_STATE_DIR": str(tmp_path / "state"),
                "CAMP_CONFIG_DIR": str(camp_config_dir),
            },
        )
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}: {result.stderr}"
        assert result.stderr == "", f"Expected empty stderr, got: {result.stderr!r}"

    def test_slug_none_repo_root_exits_0_no_stderr(self, tmp_path: Path):
        """cwd = repo root (no worktree segment) → slug=None → exit 0, no stderr."""
        camp_config_dir = tmp_path / "camp-config"
        groups_dir = camp_config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)

        # Create a member repo
        member_repo = tmp_path / "member_repo"
        _init_git_repo(member_repo)

        toml_content = f"""
[group]
name = "mygroup"

[[members]]
name = "member"
repo_root = "{member_repo!s}"
bootstrap = []
"""
        (groups_dir / "mygroup.toml").write_text(toml_content)

        # cwd = member_repo root itself (not inside .claude/worktrees/<slug>)
        result = _run_session_bootstrap(
            cwd=str(member_repo),
            extra_env={
                "CAMP_STATE_DIR": str(tmp_path / "state"),
                "CAMP_CONFIG_DIR": str(camp_config_dir),
            },
        )
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}: {result.stderr}"
        assert result.stderr == "", f"Expected empty stderr, got: {result.stderr!r}"


class TestSessionBootstrapIdempotent:
    def test_second_run_is_noop(self, tmp_path: Path):
        """session-bootstrap is idempotent — second run a no-op on an existing worktree."""
        camp_config_dir = tmp_path / "camp-config"
        groups_dir = camp_config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)

        member_repo = tmp_path / "member_repo"
        _init_git_repo(member_repo)

        toml_content = f"""
[group]
name = "mygroup"

[[members]]
name = "member"
repo_root = "{member_repo!s}"
bootstrap = []
"""
        (groups_dir / "mygroup.toml").write_text(toml_content)

        # Unified layout: the workspace member dir exists so cwd resolves to a
        # real (group, slug); the existence-guarded reconcile is a no-op.
        state_dir = tmp_path / "state"
        wt_path = state_dir / "mygroup" / "worktrees" / "feat-x" / "member"
        wt_path.mkdir(parents=True, exist_ok=True)

        # Run from inside the workspace member dir
        result = _run_session_bootstrap(
            cwd=str(wt_path),
            extra_env={
                "CAMP_STATE_DIR": str(state_dir),
                "CAMP_CONFIG_DIR": str(camp_config_dir),
            },
        )
        # Should exit 0 (reconcile is idempotent)
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Test 4: import guard — bare python3, not ModuleNotFoundError
# ---------------------------------------------------------------------------


class TestBootstrapGuard:
    def test_bare_python3_no_module_not_found_error(self, tmp_path: Path):
        """Running session-bootstrap via bare python3 (no .pth) doesn't leak ModuleNotFoundError."""
        # Run via bare python3 + the cli/camp script directly
        cli_camp = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "cli" / "camp"

        # Use a stripped env (no TRAILHEAD_ROOT, no site-packages, but keep PATH for git/python3)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(tmp_path)),
            "CAMP_STATE_DIR": str(tmp_path / "state"),
            "CAMP_CONFIG_DIR": str(tmp_path / "nonexistent"),
        }

        result = subprocess.run(
            ["python3", str(cli_camp), "session-bootstrap"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            env=env,
        )
        # Must NOT contain a raw ModuleNotFoundError traceback
        assert "ModuleNotFoundError" not in result.stderr, (
            f"Raw ModuleNotFoundError leaked to stderr:\n{result.stderr}"
        )
        assert "Traceback" not in result.stderr or "trailhead" not in result.stderr, (
            f"Raw traceback with trailhead import leaked:\n{result.stderr}"
        )
        # Either resolves (exit 0) or gives a legible error (exit 1 with a helpful message)
        if result.returncode != 0:
            assert "trailhead" in result.stderr.lower() or "TRAILHEAD_ROOT" in result.stderr, (
                f"Unexpected error: {result.stderr}"
            )


# ---------------------------------------------------------------------------
# Test 5: worktree-cleanup handler
# ---------------------------------------------------------------------------


def _run_worktree_cleanup(
    cwd: str,
    extra_env: dict[str, str] | None = None,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """Run camp worktree-cleanup via the bin/camp wrapper."""
    env = {**os.environ}
    if extra_env:
        env.update(extra_env)
    cmd = [str(_BIN_CAMP), "worktree-cleanup"] + (extra_args or [])
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


class TestWorktreeCleanup:
    def test_removes_member_worktrees_and_manifest(self, tmp_path: Path):
        """worktree-cleanup removes member worktrees + central manifest."""
        from camp.provision.reconcile import reconcile_worktree
        from camp.group.manifest import manifest_path_for

        # CAMP_CONFIG_DIR override: config_dir("camp") returns the override directly.
        # groups/ lives at <camp_config_dir>/groups, not <camp_config_dir>/camp/groups.
        camp_config_dir = tmp_path / "camp-config"
        groups_dir = camp_config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)

        repo_a = tmp_path / "repo_a"
        repo_b = tmp_path / "repo_b"
        _init_git_repo(repo_a)
        _init_git_repo(repo_b)

        env = {
            "CAMP_STATE_DIR": str(tmp_path / "camp-state"),
            "CAMP_CONFIG_DIR": str(camp_config_dir),
        }

        group = _make_group_config(
            "cleanup-group",
            [
                {"name": "repo_a", "repo_root": str(repo_a), "bootstrap": []},
                {"name": "repo_b", "repo_root": str(repo_b), "bootstrap": []},
            ],
        )

        # Create worktrees via reconcile (using the module, not the CLI)
        state_env = {"CAMP_STATE_DIR": str(tmp_path / "camp-state")}
        reconcile_worktree(group, "feat-y", env=state_env)

        wt_a = _member_wt("cleanup-group", "feat-y", "repo_a", state_env)
        wt_b = _member_wt("cleanup-group", "feat-y", "repo_b", state_env)
        assert wt_a.is_dir()
        assert wt_b.is_dir()

        # Write the TOML config so the CLI can find the group
        toml_content = f"""
[group]
name = "cleanup-group"

[[members]]
name = "repo_a"
repo_root = "{repo_a!s}"
bootstrap = []

[[members]]
name = "repo_b"
repo_root = "{repo_b!s}"
bootstrap = []
"""
        (groups_dir / "cleanup-group.toml").write_text(toml_content)

        # Run worktree-cleanup from inside wt_a (so resolution finds the group + slug)
        result = _run_worktree_cleanup(
            cwd=str(wt_a),
            extra_env=env,
        )
        assert result.returncode == 0, f"worktree-cleanup failed: {result.stderr}"

        # Both worktrees should be gone
        assert not wt_a.is_dir(), "wt_a should have been removed"
        assert not wt_b.is_dir(), "wt_b should have been removed"

        # Central manifest should be gone
        mpath = manifest_path_for(
            "cleanup-group",
            "feat-y",
            env={"CAMP_STATE_DIR": str(tmp_path / "camp-state")},
        )
        assert not mpath.exists(), f"Manifest should have been removed: {mpath}"

    def test_dirty_worktree_blocks_cleanup_without_force(self, tmp_path: Path):
        """A dirty worktree blocks cleanup unless --force."""
        from camp.provision.reconcile import reconcile_worktree

        camp_config_dir = tmp_path / "camp-config"
        groups_dir = camp_config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)

        repo_a = tmp_path / "repo_a"
        _init_git_repo(repo_a)

        env = {
            "CAMP_STATE_DIR": str(tmp_path / "camp-state"),
            "CAMP_CONFIG_DIR": str(camp_config_dir),
        }

        group = _make_group_config(
            "dirty-group",
            [{"name": "repo_a", "repo_root": str(repo_a), "bootstrap": []}],
        )

        state_env = {"CAMP_STATE_DIR": str(tmp_path / "camp-state")}
        reconcile_worktree(group, "feat-dirty", env=state_env)

        wt_a = _member_wt("dirty-group", "feat-dirty", "repo_a", state_env)
        assert wt_a.is_dir()

        # Make the worktree dirty
        dirty_file = wt_a / "dirty.txt"
        dirty_file.write_text("dirty change\n")

        toml_content = f"""
[group]
name = "dirty-group"

[[members]]
name = "repo_a"
repo_root = "{repo_a!s}"
bootstrap = []
"""
        (groups_dir / "dirty-group.toml").write_text(toml_content)

        # Without --force, should fail (non-zero exit) with a meaningful error
        result = _run_worktree_cleanup(
            cwd=str(wt_a),
            extra_env=env,
        )
        assert result.returncode != 0, (
            f"Expected non-zero exit for dirty worktree, got 0. stdout={result.stdout}"
        )
        assert wt_a.is_dir(), "Dirty worktree should NOT have been removed"

    def test_dirty_worktree_cleanup_succeeds_with_force(self, tmp_path: Path):
        """--force removes a dirty worktree."""
        from camp.provision.reconcile import reconcile_worktree

        camp_config_dir = tmp_path / "camp-config"
        groups_dir = camp_config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)

        repo_a = tmp_path / "repo_a"
        _init_git_repo(repo_a)

        env = {
            "CAMP_STATE_DIR": str(tmp_path / "camp-state"),
            "CAMP_CONFIG_DIR": str(camp_config_dir),
        }

        group = _make_group_config(
            "force-group",
            [{"name": "repo_a", "repo_root": str(repo_a), "bootstrap": []}],
        )

        state_env = {"CAMP_STATE_DIR": str(tmp_path / "camp-state")}
        reconcile_worktree(group, "feat-force", env=state_env)

        wt_a = _member_wt("force-group", "feat-force", "repo_a", state_env)
        assert wt_a.is_dir()

        # Make it dirty
        (wt_a / "dirty.txt").write_text("dirty\n")

        toml_content = f"""
[group]
name = "force-group"

[[members]]
name = "repo_a"
repo_root = "{repo_a!s}"
bootstrap = []
"""
        (groups_dir / "force-group.toml").write_text(toml_content)

        result = _run_worktree_cleanup(
            cwd=str(wt_a),
            extra_env=env,
            extra_args=["--force"],
        )
        assert result.returncode == 0, f"Expected success with --force: {result.stderr}"
        assert not wt_a.is_dir(), "Worktree should have been removed with --force"

    def test_non_member_cwd_silent_noop(self, tmp_path: Path):
        """Non-member cwd → silent no-op: exit 0, empty stderr."""
        camp_config_dir = tmp_path / "camp-config"
        groups_dir = camp_config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)

        other_repo = tmp_path / "other"
        _init_git_repo(other_repo)

        toml_content = f"""
[group]
name = "agroup"

[[members]]
name = "other"
repo_root = "{other_repo!s}"
bootstrap = []
"""
        (groups_dir / "agroup.toml").write_text(toml_content)

        unrelated = tmp_path / "unrelated"
        unrelated.mkdir(parents=True, exist_ok=True)

        result = _run_worktree_cleanup(
            cwd=str(unrelated),
            extra_env={
                "CAMP_STATE_DIR": str(tmp_path / "state"),
                "CAMP_CONFIG_DIR": str(camp_config_dir),
            },
        )
        assert result.returncode == 0, f"Expected exit 0, got: {result.returncode}: {result.stderr}"
        assert result.stderr == "", f"Expected empty stderr, got: {result.stderr!r}"


# ---------------------------------------------------------------------------
# session-bootstrap warns on genuine reconcile failure
# ---------------------------------------------------------------------------


class TestSessionBootstrapGenuineFailure:
    """session-bootstrap emits a one-line warning on genuine reconcile failure
    (cwd resolves to a valid member worktree but reconcile_worktree raises),
    and exits 0 — no traceback.
    """

    def test_genuine_reconcile_failure_warns_once_names_slug_exits_0(self, tmp_path: Path):
        """A genuine reconcile failure in a valid member worktree emits exactly one
        stderr line naming the slug, exits 0, and contains no traceback.
        """
        camp_config_dir = tmp_path / "camp-config"
        groups_dir = camp_config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)

        member_repo = tmp_path / "member_repo"
        _init_git_repo(member_repo)

        # A bootstrap that always fails → reconcile raises naming the member/slug.
        toml_content = f"""
[group]
name = "warngroup"

[[members]]
name = "member"
repo_root = "{member_repo!s}"
bootstrap = ["false"]
"""
        (groups_dir / "warngroup.toml").write_text(toml_content)

        # Unified layout: the workspace member dir exists so cwd resolves to a
        # real (group, slug) pair. The existence-guarded `git worktree add` is a
        # no-op; the failing bootstrap is what triggers the reconcile failure.
        state_dir = tmp_path / "state"
        wt_path = state_dir / "warngroup" / "worktrees" / "feat-warn" / "member"
        wt_path.mkdir(parents=True, exist_ok=True)

        result = _run_session_bootstrap(
            cwd=str(wt_path),
            extra_env={
                "CAMP_STATE_DIR": str(state_dir),
                "CAMP_CONFIG_DIR": str(camp_config_dir),
            },
        )

        assert result.returncode == 0, (
            f"Expected exit 0 (non-blocking), got {result.returncode}. stderr={result.stderr!r}"
        )

        stderr_lines = [ln for ln in result.stderr.splitlines() if ln.strip()]
        assert len(stderr_lines) == 1, (
            f"Expected exactly one stderr warning line, got {len(stderr_lines)}: {result.stderr!r}"
        )

        warning = stderr_lines[0]
        assert "feat-warn" in warning, f"Warning must name the slug 'feat-warn', got: {warning!r}"
        assert "Traceback" not in result.stderr, (
            f"Traceback must not appear in stderr: {result.stderr!r}"
        )

    def test_no_op_cases_remain_silent_after_change(self, tmp_path: Path):
        """The four no-op cases still produce no stderr after the reconcile-failure
        warning is added. Regression guard.
        """
        # Case: cold-start (no config dir at all)
        nonexistent = str(tmp_path / "nonexistent")
        result = _run_session_bootstrap(
            cwd=str(tmp_path),
            extra_env={
                "CAMP_STATE_DIR": nonexistent,
                "CAMP_CONFIG_DIR": nonexistent,
            },
        )
        assert result.returncode == 0
        assert result.stderr == "", f"Cold-start case must be silent, got: {result.stderr!r}"


# ---------------------------------------------------------------------------
# Boot-path budget: a task already recorded over-budget by a prior run stays
# quiet and unexecuted on this (genuinely fresh-process) hook invocation.
# ---------------------------------------------------------------------------


class TestSessionBootstrapOverBudgetSkip:
    def test_hook_stays_silent_and_exits_0_for_a_task_already_over_budget(
        self, tmp_path: Path
    ):
        """A task a prior run recorded over-budget is skip-worthy on the hook
        path: this fresh `camp session-bootstrap` process neither re-runs it
        nor re-emits the misclassification message. Exit 0, empty stderr —
        the "no-op" contract holds for this path too."""
        from camp.group.manifest import manifest_path_for, read_central_manifest, write_central_manifest

        camp_config_dir = tmp_path / "camp-config"
        groups_dir = camp_config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)

        member_repo = tmp_path / "member_repo"
        _init_git_repo(member_repo)

        toml_content = f"""
[group]
name = "overbudgetskipg"

[[members]]
name = "member"
repo_root = "{member_repo!s}"
bootstrap = []
tasks = ["graph-build"]

[tasks.graph-build]
phase = "provision"

[[tasks.graph-build.steps]]
name = "seed"
cmd = ["true"]
"""
        (groups_dir / "overbudgetskipg.toml").write_text(toml_content)

        state_dir = tmp_path / "state"
        wt_path = state_dir / "overbudgetskipg" / "worktrees" / "feat-budget" / "member"
        wt_path.mkdir(parents=True, exist_ok=True)

        env = {
            "CAMP_STATE_DIR": str(state_dir),
            "CAMP_CONFIG_DIR": str(camp_config_dir),
        }

        mpath = manifest_path_for("overbudgetskipg", "feat-budget", env=env)
        write_central_manifest(
            mpath,
            {
                "schema_version": 1,
                "group": "overbudgetskipg",
                "slug": "feat-budget",
                "branch": "worktree-feat-budget",
                "members": [
                    {
                        "name": "member",
                        "repo_root": str(member_repo),
                        "worktree_path": str(wt_path),
                        "tasks": {"graph-build": {"state": "over-budget"}},
                    }
                ],
            },
        )

        result = _run_session_bootstrap(cwd=str(wt_path), extra_env=env)

        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}: {result.stderr}"
        assert result.stderr == "", f"Expected silence, got: {result.stderr!r}"

        entry = read_central_manifest(mpath)["members"][0]
        assert entry["tasks"]["graph-build"]["state"] == "over-budget", (
            "the over-budget state must survive verbatim, not be re-run or normalized"
        )


# ---------------------------------------------------------------------------
# The SessionStart capability report
# ---------------------------------------------------------------------------


def _activate_task(
    name: str, *, required: bool = False, capability: str | None = None
) -> dict[str, Any]:
    """Build a member activate-phase task in the config-resolved shape."""
    task: dict[str, Any] = {
        "name": name,
        "phase": "activate",
        "required": required,
        "timeout_seconds": None,
        "steps": [{"name": name, "cmd": ["true"]}],
    }
    if capability is not None:
        task["capability"] = capability
    return task


def _capability_manifest(
    *,
    group_name: str,
    slug: str,
    members: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "group": group_name,
        "slug": slug,
        "branch": f"worktree-{slug}",
        "members": members,
    }


class TestCapabilityReport:
    """Unit tests against camp.launch.hook_handlers.capability_report — the pure
    function that turns a fresh manifest read into the SessionStart capability
    report text (or "" when nothing is outstanding/failed).
    """

    def test_outstanding_activate_task_with_declared_capability_uses_it_verbatim(
        self, tmp_path: Path
    ):
        """An activate-phase task that never ran (not recorded 'ok') and
        declares a `capability` string produces a line stating that exact
        consequence — not the generic task-name-plus-boilerplate fallback. An
        agent that reverts to the generic-only line for a task with a
        declared capability must fail this assertion."""
        from camp.launch.hook_handlers import capability_report
        from camp.group.manifest import manifest_path_for, write_central_manifest

        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        capability_text = (
            "dependencies are still installing — test and build commands will fail "
            "until they finish"
        )
        group = _make_group_config(
            "capgroup",
            [
                {
                    "name": "repo_a",
                    "repo_root": "/x",
                    "tasks": [_activate_task("dep-install", capability=capability_text)],
                }
            ],
        )
        mpath = manifest_path_for("capgroup", "feat-cap", env=env)
        write_central_manifest(
            mpath,
            _capability_manifest(
                group_name="capgroup",
                slug="feat-cap",
                members=[
                    {
                        "name": "repo_a",
                        "repo_root": "/x",
                        "worktree_path": "/x",
                        "provision_state": "ready",
                        "work_state": "pending",
                        "tasks": {},
                    }
                ],
            ),
        )

        report = capability_report(group, "feat-cap", env=env)

        assert capability_text in report, "declared capability text must appear verbatim"
        assert "has not finished yet" not in report, (
            "a declared capability must replace the generic boilerplate line, not "
            "merely be appended alongside it"
        )

    def test_outstanding_activate_task_without_capability_falls_back_to_generic_line(
        self, tmp_path: Path
    ):
        """An activate-phase task that never ran and declares no `capability`
        string still produces today's generic line — the fallback stays."""
        from camp.launch.hook_handlers import capability_report
        from camp.group.manifest import manifest_path_for, write_central_manifest

        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        group = _make_group_config(
            "capgroup",
            [{"name": "repo_a", "repo_root": "/x", "tasks": [_activate_task("dep-install")]}],
        )
        mpath = manifest_path_for("capgroup", "feat-cap-fallback", env=env)
        write_central_manifest(
            mpath,
            _capability_manifest(
                group_name="capgroup",
                slug="feat-cap-fallback",
                members=[
                    {
                        "name": "repo_a",
                        "repo_root": "/x",
                        "worktree_path": "/x",
                        "provision_state": "ready",
                        "work_state": "pending",
                        "tasks": {},
                    }
                ],
            ),
        )

        report = capability_report(group, "feat-cap-fallback", env=env)

        assert report, "expected a non-empty capability report"
        assert "dep-install" in report
        assert report.strip() not in ("pending", "repo_a: pending", "dep-install: pending")
        assert "has not finished yet" in report

    def test_every_member_ready_emits_nothing(self, tmp_path: Path):
        """With every member boot-ready and work-ready, nothing is emitted —
        no all-clear line."""
        from camp.launch.hook_handlers import capability_report
        from camp.group.manifest import manifest_path_for, write_central_manifest

        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        group = _make_group_config(
            "capgroup2",
            [{"name": "repo_a", "repo_root": "/x", "tasks": [_activate_task("dep-install")]}],
        )
        mpath = manifest_path_for("capgroup2", "feat-ready", env=env)
        write_central_manifest(
            mpath,
            _capability_manifest(
                group_name="capgroup2",
                slug="feat-ready",
                members=[
                    {
                        "name": "repo_a",
                        "repo_root": "/x",
                        "worktree_path": "/x",
                        "provision_state": "ready",
                        "work_state": "ready",
                        "tasks": {"dep-install": {"state": "ok"}},
                    }
                ],
            ),
        )

        report = capability_report(group, "feat-ready", env=env)

        assert report == "", f"expected no all-clear, got: {report!r}"

    def test_no_activate_tasks_declared_emits_nothing(self, tmp_path: Path):
        """A member that declares no activate-phase task never produces a line,
        even though it has no recorded work_state."""
        from camp.launch.hook_handlers import capability_report
        from camp.group.manifest import manifest_path_for, write_central_manifest

        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        group = _make_group_config(
            "capgroup3",
            [{"name": "repo_a", "repo_root": "/x", "tasks": []}],
        )
        mpath = manifest_path_for("capgroup3", "feat-na", env=env)
        write_central_manifest(
            mpath,
            _capability_manifest(
                group_name="capgroup3",
                slug="feat-na",
                members=[
                    {
                        "name": "repo_a",
                        "repo_root": "/x",
                        "worktree_path": "/x",
                        "provision_state": "ready",
                        "tasks": {},
                    }
                ],
            ),
        )

        report = capability_report(group, "feat-na", env=env)

        assert report == ""

    def test_failed_task_line_differs_from_outstanding_task_line(self, tmp_path: Path):
        """A failed work-enabling task produces a different, actionable line
        from one merely outstanding."""
        from camp.launch.hook_handlers import capability_report
        from camp.group.manifest import manifest_path_for, write_central_manifest

        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        group = _make_group_config(
            "capgroup4",
            [{"name": "repo_a", "repo_root": "/x", "tasks": [_activate_task("dep-install")]}],
        )

        mpath_pending = manifest_path_for("capgroup4", "feat-pending", env=env)
        write_central_manifest(
            mpath_pending,
            _capability_manifest(
                group_name="capgroup4",
                slug="feat-pending",
                members=[
                    {
                        "name": "repo_a",
                        "repo_root": "/x",
                        "worktree_path": "/x",
                        "provision_state": "ready",
                        "work_state": "pending",
                        "tasks": {},
                    }
                ],
            ),
        )
        pending_report = capability_report(group, "feat-pending", env=env)

        mpath_failed = manifest_path_for("capgroup4", "feat-failed", env=env)
        write_central_manifest(
            mpath_failed,
            _capability_manifest(
                group_name="capgroup4",
                slug="feat-failed",
                members=[
                    {
                        "name": "repo_a",
                        "repo_root": "/x",
                        "worktree_path": "/x",
                        "provision_state": "ready",
                        "work_state": "failed",
                        "reason": "npm ci: exit 1",
                        "tasks": {"dep-install": {"state": "failed", "reason": "exit 1"}},
                    }
                ],
            ),
        )
        failed_report = capability_report(group, "feat-failed", env=env)

        assert pending_report and failed_report
        assert pending_report != failed_report
        assert "failed" in failed_report.lower()

    def test_failed_task_line_does_not_instruct_fetching_json(self, tmp_path: Path) -> None:
        """FINDING 2 regression: the failed-task line must never tell the agent
        to fetch `camp status --json` — that surface carries unredacted
        `stderr_excerpt`, which is known to include credentials on a failed
        step (e.g. a private-registry auth failure during `npm ci`). The line
        must still say the task failed and point at a human/operator or a
        retry, just never at the raw-stderr-bearing command."""
        from camp.launch.hook_handlers import capability_report
        from camp.group.manifest import manifest_path_for, write_central_manifest

        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        group = _make_group_config(
            "capgroup4b",
            [{"name": "repo_a", "repo_root": "/x", "tasks": [_activate_task("dep-install")]}],
        )

        mpath = manifest_path_for("capgroup4b", "feat-failed", env=env)
        write_central_manifest(
            mpath,
            _capability_manifest(
                group_name="capgroup4b",
                slug="feat-failed",
                members=[
                    {
                        "name": "repo_a",
                        "repo_root": "/x",
                        "worktree_path": "/x",
                        "provision_state": "ready",
                        "work_state": "failed",
                        "reason": "npm ci: exit 1",
                        "tasks": {"dep-install": {"state": "failed", "reason": "exit 1"}},
                    }
                ],
            ),
        )
        report = capability_report(group, "feat-failed", env=env)

        assert report
        assert "--json" not in report

    def test_report_never_contains_raw_task_stderr(self, tmp_path: Path):
        """The report cites where to read task output instead of embedding it —
        task stderr is known to carry credentials on failure."""
        from camp.launch.hook_handlers import capability_report
        from camp.group.manifest import manifest_path_for, write_central_manifest

        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        group = _make_group_config(
            "capgroup5",
            [{"name": "repo_a", "repo_root": "/x", "tasks": [_activate_task("dep-install")]}],
        )
        secret = "SUPER_SECRET_TOKEN_abc123"
        mpath = manifest_path_for("capgroup5", "feat-secret", env=env)
        write_central_manifest(
            mpath,
            _capability_manifest(
                group_name="capgroup5",
                slug="feat-secret",
                members=[
                    {
                        "name": "repo_a",
                        "repo_root": "/x",
                        "worktree_path": "/x",
                        "provision_state": "ready",
                        "work_state": "failed",
                        "tasks": {
                            "dep-install": {
                                "state": "failed",
                                "reason": f"auth failed: {secret}",
                            }
                        },
                    }
                ],
            ),
        )

        report = capability_report(group, "feat-secret", env=env)

        assert secret not in report
        assert "camp status" in report, "report should cite where to read the reason"

    def test_bounded_length_ceiling_is_a_concrete_asserted_value(self):
        """The report's length ceiling is a concrete constant, so a future edit
        that grows the report has something to fail against."""
        from camp.launch import hook_handlers

        assert hook_handlers.CAPABILITY_REPORT_MAX_CHARS == 1000

    def test_overflow_degrades_to_a_summary_not_a_mid_sentence_truncation(
        self, tmp_path: Path
    ):
        """Many members carrying outstanding/failed work stay within the
        ceiling by summarizing — never a half-sentence about what the agent
        cannot do."""
        from camp.launch.hook_handlers import capability_report, CAPABILITY_REPORT_MAX_CHARS
        from camp.group.manifest import manifest_path_for, write_central_manifest

        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        member_count = 40
        members_config = [
            {
                "name": f"repo_{i}",
                "repo_root": f"/x{i}",
                "tasks": [_activate_task(f"dep-install-{i}")],
            }
            for i in range(member_count)
        ]
        group = _make_group_config("capgroup6", members_config)

        manifest_members = [
            {
                "name": f"repo_{i}",
                "repo_root": f"/x{i}",
                "worktree_path": f"/x{i}",
                "provision_state": "ready",
                "work_state": "failed",
                "tasks": {f"dep-install-{i}": {"state": "failed", "reason": "boom"}},
            }
            for i in range(member_count)
        ]
        mpath = manifest_path_for("capgroup6", "feat-overflow", env=env)
        write_central_manifest(
            mpath,
            _capability_manifest(
                group_name="capgroup6", slug="feat-overflow", members=manifest_members
            ),
        )

        report = capability_report(group, "feat-overflow", env=env)

        assert report
        assert len(report) <= CAPABILITY_REPORT_MAX_CHARS, (
            f"report exceeded its ceiling: {len(report)} chars"
        )
        assert report.rstrip().endswith((".", "!", "?")), (
            f"report must not end mid-statement: {report!r}"
        )

    def test_report_reflects_live_state_not_a_cached_snapshot(self, tmp_path: Path):
        """Change the underlying manifest state between two reads and the
        report differs — a stale report is worse than none."""
        from camp.launch.hook_handlers import capability_report
        from camp.group.manifest import manifest_path_for, write_central_manifest

        env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
        group = _make_group_config(
            "capgroup7",
            [{"name": "repo_a", "repo_root": "/x", "tasks": [_activate_task("dep-install")]}],
        )
        mpath = manifest_path_for("capgroup7", "feat-live", env=env)

        def _write(work_state: str, task_state: str) -> None:
            write_central_manifest(
                mpath,
                _capability_manifest(
                    group_name="capgroup7",
                    slug="feat-live",
                    members=[
                        {
                            "name": "repo_a",
                            "repo_root": "/x",
                            "worktree_path": "/x",
                            "provision_state": "ready",
                            "work_state": work_state,
                            "tasks": {"dep-install": {"state": task_state}},
                        }
                    ],
                ),
            )

        _write("pending", "pending")
        first = capability_report(group, "feat-live", env=env)
        assert first, "expected an outstanding-work report on the first read"

        _write("ready", "ok")
        second = capability_report(group, "feat-live", env=env)

        assert second != first
        assert second == "", f"dependencies arrived — expected silence, got: {second!r}"

    def test_internal_failure_returns_empty_string_not_a_raised_exception(
        self, tmp_path: Path, monkeypatch
    ):
        """Every failure path inside report generation exits quietly — an
        internal exception must not propagate and crash session start."""
        import camp.provision.lifecycle as lifecycle_mod
        from camp.launch.hook_handlers import capability_report

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated internal failure")

        monkeypatch.setattr(lifecycle_mod, "provision_status_code", _boom)

        report = capability_report({"group": {"name": "g"}, "members": []}, "slug")

        assert report == ""


class TestSessionBootstrapCapabilityReportIntegration:
    """End-to-end: `camp session-bootstrap` emits the capability report as the
    SessionStart additionalContext JSON contract when there is outstanding
    work-enabling work, and nothing when a member is unrelated to any group.
    """

    def test_hook_emits_additional_context_for_outstanding_activate_task(
        self, tmp_path: Path
    ):
        camp_config_dir = tmp_path / "camp-config"
        groups_dir = camp_config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)

        member_repo = tmp_path / "member_repo"
        _init_git_repo(member_repo)

        toml_content = f"""
[group]
name = "hookcapgroup"

[[members]]
name = "member"
repo_root = "{member_repo!s}"
bootstrap = []
tasks = ["dep-install"]

[tasks.dep-install]
phase = "activate"

[[tasks.dep-install.steps]]
name = "install"
cmd = ["true"]
"""
        (groups_dir / "hookcapgroup.toml").write_text(toml_content)

        state_dir = tmp_path / "state"
        env = {
            "CAMP_STATE_DIR": str(state_dir),
            "CAMP_CONFIG_DIR": str(camp_config_dir),
        }

        from camp.group.manifest import manifest_path_for, write_central_manifest

        wt_path = state_dir / "hookcapgroup" / "worktrees" / "feat-hookcap" / "member"
        wt_path.mkdir(parents=True, exist_ok=True)

        mpath = manifest_path_for("hookcapgroup", "feat-hookcap", env=env)
        write_central_manifest(
            mpath,
            {
                "schema_version": 1,
                "group": "hookcapgroup",
                "slug": "feat-hookcap",
                "branch": "worktree-feat-hookcap",
                "members": [
                    {
                        "name": "member",
                        "repo_root": str(member_repo),
                        "worktree_path": str(wt_path),
                        "provision_state": "ready",
                        "work_state": "pending",
                        "tasks": {},
                    }
                ],
            },
        )

        result = _run_session_bootstrap(cwd=str(wt_path), extra_env=env)

        assert result.returncode == 0, f"stderr={result.stderr!r}"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "dep-install" in payload["hookSpecificOutput"]["additionalContext"]

    def test_unrelated_repo_emits_no_capability_report(self, tmp_path: Path):
        """A session in a repo that is not a camp member gets nothing — no
        capability report stdout at all."""
        unrelated_repo = tmp_path / "unrelated"
        unrelated_repo.mkdir(parents=True, exist_ok=True)

        result = _run_session_bootstrap(
            cwd=str(unrelated_repo),
            extra_env={
                "CAMP_STATE_DIR": str(tmp_path / "nonexistent-state"),
                "CAMP_CONFIG_DIR": str(tmp_path / "nonexistent-config"),
            },
        )

        assert result.returncode == 0
        assert result.stdout == "", f"expected no stdout, got: {result.stdout!r}"


# ---------------------------------------------------------------------------
# camp --help lists group (renamed from init)
# ---------------------------------------------------------------------------


class TestHelpMenuInit:
    """camp --help output contains 'group' (renamed from 'init') with a description,
    exits 0, is not an argparse dump.
    """

    def test_help_contains_init(self):
        """camp --help output contains 'group' (renamed from 'init') with a description."""
        result = subprocess.run(
            [str(_BIN_CAMP), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Expected exit 0 from --help, got {result.returncode}: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        # The subcommand is `group`.
        assert "group" in combined, f"'group' not found in --help output:\n{combined}"

    def test_help_init_has_description(self):
        """camp --help output describes what group does."""
        result = subprocess.run(
            [str(_BIN_CAMP), "--help"],
            capture_output=True,
            text=True,
        )
        combined = result.stdout + result.stderr
        # The subcommand is `group`.
        assert "group" in combined
        # Check for keywords that would appear in a one-liner description
        init_description_words = ["Wire", "wire", "hook", "Hook", "Setup", "setup", "config"]
        assert any(w in combined for w in init_description_words), (
            f"Expected a description near 'group' in --help, "
            f"but none of {init_description_words} found:\n{combined}"
        )

    def test_help_not_argparse_dump(self):
        """camp --help is the curated menu, not an argparse dump."""
        result = subprocess.run(
            [str(_BIN_CAMP), "--help"],
            capture_output=True,
            text=True,
        )
        combined = result.stdout + result.stderr
        # argparse dumps start with 'usage: camp' and contain 'optional arguments:'
        # or 'options:'; curated help has 'camp —'
        assert "optional arguments:" not in combined, "argparse dump detected in --help output"
        assert "positional arguments:" not in combined, "argparse dump detected in --help output"

    def test_help_does_not_list_session_bootstrap_or_worktree_cleanup(self):
        """session-bootstrap and worktree-cleanup are NOT in the help menu
        (they're hook-internal).
        """
        result = subprocess.run(
            [str(_BIN_CAMP), "--help"],
            capture_output=True,
            text=True,
        )
        combined = result.stdout + result.stderr
        assert "session-bootstrap" not in combined, (
            f"session-bootstrap should not appear in --help: {combined!r}"
        )
        assert "worktree-cleanup" not in combined, (
            f"worktree-cleanup should not appear in --help: {combined!r}"
        )
