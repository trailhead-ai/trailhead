"""Tests for camp init + SessionStart hook wiring.

Slice 2 drops the WorktreeRemove hook wiring (camp owns teardown via `camp rm`);
only the SessionStart hook is written into each member's .claude/settings.json.
The `worktree-cleanup` handler itself is retained (still invocable) but no longer
auto-wired. Member worktrees now live under the unified workspace layout
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

7. D-H under a bare python3: running session-bootstrap from a context where
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
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"
_BIN_CAMP = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Helpers shared with slice2 tests
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    """Initialize a real git repo at path with an initial commit."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    readme = path / "README.md"
    readme.write_text("# test\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init", "--no-gpg-sign"],
                   check=True, capture_output=True)


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
    from group_resolve import central_state_dir

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
        from hooks_writer import write_hooks_for_member

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
        commands = [
            h.get("command", "")
            for entry in ss_hooks
            for h in entry.get("hooks", [])
        ]
        assert any("session-bootstrap" in cmd for cmd in commands)

    def test_does_not_write_worktree_remove_hook(self, two_member_group):
        """Slice 2 drops WorktreeRemove wiring — camp owns teardown via `camp rm`.

        No WorktreeRemove entry must be written into the member's settings.json.
        """
        from hooks_writer import write_hooks_for_member

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
        from hooks_writer import write_hooks_for_member

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
        from hooks_writer import write_hooks_for_member

        g = two_member_group
        repo_a = g["repo_a"]
        camp_bin = str(_BIN_CAMP)

        write_hooks_for_member(repo_a, camp_bin)

        settings_path = repo_a / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text())

        hooks = data.get("hooks", {})
        ss_hooks = hooks.get("SessionStart", [])
        commands = [
            h.get("command", "")
            for entry in ss_hooks
            for h in entry.get("hooks", [])
        ]
        expected_cmd = f"${{CAMP_BIN:-{camp_bin}}} session-bootstrap"
        assert expected_cmd in commands, (
            f"Expected command {expected_cmd!r} not found in {commands}"
        )

    def test_idempotent_no_duplicates(self, two_member_group):
        """Re-running write_hooks_for_member adds NO duplicate hook entries."""
        from hooks_writer import write_hooks_for_member

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

        ss_commands = [
            h.get("command", "")
            for entry in ss_hooks
            for h in entry.get("hooks", [])
        ]

        expected_ss = f"${{CAMP_BIN:-{camp_bin}}} session-bootstrap"

        assert ss_commands.count(expected_ss) == 1, (
            f"Duplicate session-bootstrap entries: {ss_commands}"
        )
        # WorktreeRemove wiring is dropped in Slice 2.
        assert "WorktreeRemove" not in hooks

    def test_preserves_existing_unrelated_keys(self, two_member_group):
        """An existing settings.json with unrelated keys is preserved after write."""
        from hooks_writer import write_hooks_for_member

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
                "PreToolUse": [
                    {"hooks": [{"type": "command", "command": "/some/other/hook.sh"}]}
                ]
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
        from hooks_writer import write_hooks_for_member

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
        commands = [
            h.get("command", "")
            for entry in ss_hooks
            for h in entry.get("hooks", [])
        ]
        assert any(camp_bin_spaced in cmd for cmd in commands), (
            f"Expected camp_bin path in commands: {commands}"
        )


# ---------------------------------------------------------------------------
# Test 2: init_cmd writes hooks for all members in the group
# ---------------------------------------------------------------------------


class TestInitCmd:
    def test_init_writes_hooks_for_all_members(self, two_member_group):
        """camp init <group> writes hook entries to each member's .claude/settings.json."""
        from init_cmd import run_init

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
        from init_cmd import run_init

        g = two_member_group
        camp_bin = str(_BIN_CAMP)

        run_init(g["group"], camp_bin)
        run_init(g["group"], camp_bin)

        for repo_key in ("repo_a", "repo_b"):
            settings_path = g[repo_key] / ".claude" / "settings.json"
            data = json.loads(settings_path.read_text())

            ss_hooks = data.get("hooks", {}).get("SessionStart", [])
            commands = [
                h.get("command", "")
                for entry in ss_hooks
                for h in entry.get("hooks", [])
            ]
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
# Test 4: D-H guard — bare python3, not ModuleNotFoundError
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
        from reconcile import reconcile_worktree
        from manifest import manifest_path_for

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
            "cleanup_group",
            [
                {"name": "repo_a", "repo_root": str(repo_a), "bootstrap": []},
                {"name": "repo_b", "repo_root": str(repo_b), "bootstrap": []},
            ],
        )

        # Create worktrees via reconcile (using the module, not the CLI)
        state_env = {"CAMP_STATE_DIR": str(tmp_path / "camp-state")}
        reconcile_worktree(group, "feat-y", env=state_env)

        wt_a = _member_wt("cleanup_group", "feat-y", "repo_a", state_env)
        wt_b = _member_wt("cleanup_group", "feat-y", "repo_b", state_env)
        assert wt_a.is_dir()
        assert wt_b.is_dir()

        # Write the TOML config so the CLI can find the group
        toml_content = f"""
[group]
name = "cleanup_group"

[[members]]
name = "repo_a"
repo_root = "{repo_a!s}"
bootstrap = []

[[members]]
name = "repo_b"
repo_root = "{repo_b!s}"
bootstrap = []
"""
        (groups_dir / "cleanup_group.toml").write_text(toml_content)

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
            "cleanup_group", "feat-y",
            env={"CAMP_STATE_DIR": str(tmp_path / "camp-state")},
        )
        assert not mpath.exists(), f"Manifest should have been removed: {mpath}"

    def test_dirty_worktree_blocks_cleanup_without_force(self, tmp_path: Path):
        """A dirty worktree blocks cleanup unless --force."""
        from reconcile import reconcile_worktree

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
            "dirty_group",
            [{"name": "repo_a", "repo_root": str(repo_a), "bootstrap": []}],
        )

        state_env = {"CAMP_STATE_DIR": str(tmp_path / "camp-state")}
        reconcile_worktree(group, "feat-dirty", env=state_env)

        wt_a = _member_wt("dirty_group", "feat-dirty", "repo_a", state_env)
        assert wt_a.is_dir()

        # Make the worktree dirty
        dirty_file = wt_a / "dirty.txt"
        dirty_file.write_text("dirty change\n")

        toml_content = f"""
[group]
name = "dirty_group"

[[members]]
name = "repo_a"
repo_root = "{repo_a!s}"
bootstrap = []
"""
        (groups_dir / "dirty_group.toml").write_text(toml_content)

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
        from reconcile import reconcile_worktree

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
            "force_group",
            [{"name": "repo_a", "repo_root": str(repo_a), "bootstrap": []}],
        )

        state_env = {"CAMP_STATE_DIR": str(tmp_path / "camp-state")}
        reconcile_worktree(group, "feat-force", env=state_env)

        wt_a = _member_wt("force_group", "feat-force", "repo_a", state_env)
        assert wt_a.is_dir()

        # Make it dirty
        (wt_a / "dirty.txt").write_text("dirty\n")

        toml_content = f"""
[group]
name = "force_group"

[[members]]
name = "repo_a"
repo_root = "{repo_a!s}"
bootstrap = []
"""
        (groups_dir / "force_group.toml").write_text(toml_content)

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
# Refinement 1: session-bootstrap warns on genuine reconcile failure
# ---------------------------------------------------------------------------


class TestSessionBootstrapGenuineFailure:
    """session-bootstrap emits a one-line warning on genuine reconcile failure
    (cwd resolves to a valid member worktree but reconcile_worktree raises),
    and exits 0 — no traceback.
    """

    def test_genuine_reconcile_failure_warns_once_names_slug_exits_0(
        self, tmp_path: Path
    ):
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
            f"Expected exit 0 (non-blocking), got {result.returncode}. "
            f"stderr={result.stderr!r}"
        )

        stderr_lines = [ln for ln in result.stderr.splitlines() if ln.strip()]
        assert len(stderr_lines) == 1, (
            f"Expected exactly one stderr warning line, got {len(stderr_lines)}: "
            f"{result.stderr!r}"
        )

        warning = stderr_lines[0]
        assert "feat-warn" in warning, (
            f"Warning must name the slug 'feat-warn', got: {warning!r}"
        )
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
        assert result.stderr == "", (
            f"Cold-start case must be silent, got: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Refinement 2: camp --help lists group (renamed from init in Slice 1)
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
        # Slice 1: 'init' renamed to 'group'.
        assert "group" in combined, (
            f"'group' not found in --help output:\n{combined}"
        )

    def test_help_init_has_description(self):
        """camp --help output describes what group (formerly init) does."""
        result = subprocess.run(
            [str(_BIN_CAMP), "--help"],
            capture_output=True,
            text=True,
        )
        combined = result.stdout + result.stderr
        # Slice 1: 'init' renamed to 'group'.
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
        assert "optional arguments:" not in combined, (
            "argparse dump detected in --help output"
        )
        assert "positional arguments:" not in combined, (
            "argparse dump detected in --help output"
        )

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
