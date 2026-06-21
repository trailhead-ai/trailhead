"""camp rm CLI integration: wiring to reconcile_break.

Test contract (TDD — all must RED before implementation, GREEN after):

1. camp rm <slug> invokes the real reconcile_break for the resolved group/slug
   (not the stub). Exit 0 on success; manifest removed.
2. dirty worktree is blocked without --force (non-zero, legible error).
3. dirty worktree succeeds with --force.
4. unknown slug → legible error, non-zero exit.
5. camp rm --name <slug> resolves via the --name flag.
6. camp rm with no slug and cwd outside a workspace → legible error.
7. The Slice-1 stub tests in test_slice1_cli_surface.py for camp rm have been
   replaced: camp rm via the group-aware path now exits 0 on a clean workspace,
   not non-zero with a stub message.

Pattern: fake-git + tmp_path + CAMP_STATE_DIR/CAMP_CONFIG_DIR (no real
~/.claude touched; no real claude exec needed for rm).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"
_SCRIPTS_DIR = _PLUGIN_DIR / "scripts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init", "--no-gpg-sign"],
                   check=True, capture_output=True)
    # Self-origin so the configured base `origin/main` resolves locally (a real
    # member always has a fetchable/resolvable base).
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", str(path)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "fetch", "origin", "--quiet"],
                   check=True, capture_output=True)


def _wait_provisioned(manifest_path: Path, members: list[str], timeout: float = 20.0) -> None:
    """Poll the manifest until all named members reach 'ready' state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = json.loads(manifest_path.read_text())
            states = {m["name"]: m["provision_state"] for m in data["members"]}
            if all(states.get(m) == "ready" for m in members):
                return
        except Exception:
            pass
        time.sleep(0.1)
    # Let the test surface the actual state
    data = json.loads(manifest_path.read_text())
    states = {m["name"]: m["provision_state"] for m in data["members"]}
    assert all(states.get(m) == "ready" for m in members), (
        f"Worktrees not ready after {timeout}s: {states}"
    )


@pytest.fixture()
def rm_env(tmp_path: Path):
    """CLI environment with two real git repos + a provisioned workspace.

    Waits for the background provisioner to complete so member worktrees
    exist on disk before returning — required for dirty-block tests.
    """
    config_dir = tmp_path / "camp-config"
    groups_dir = config_dir / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "camp-state"
    state_dir.mkdir(parents=True, exist_ok=True)

    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    _init_git_repo(repo_a)
    _init_git_repo(repo_b)

    env = {**os.environ}
    env["CAMP_CONFIG_DIR"] = str(config_dir)
    env["CAMP_STATE_DIR"] = str(state_dir)

    # Author group via real CLI
    r = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "group", "rmgroup",
         "--member", f"repo_a={repo_a}", "--member", f"repo_b={repo_b}"],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, f"group authoring failed: {r.stderr}"

    # Provision a workspace via camp ai (no harness exec needed)
    r2 = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "ai", "ws-slug", "--group", "rmgroup"],
        capture_output=True, text=True, env={**env, "CAMP_TEST_NO_EXEC": "1"},
    )
    assert r2.returncode == 0, f"camp ai failed: {r2.stderr}"

    ws_dir = state_dir / "rmgroup" / "worktrees" / "ws-slug"
    manifest = ws_dir / "manifest.json"

    # Wait for the detached background provisioner to complete so member
    # worktrees exist on disk (needed for dirty-block tests).
    _wait_provisioned(manifest, ["repo_a", "repo_b"])

    return {
        "env": env,
        "config_dir": config_dir,
        "state_dir": state_dir,
        "repo_a": repo_a,
        "repo_b": repo_b,
        "ws_dir": ws_dir,
        "tmp_path": tmp_path,
    }


def _camp(rm_env, *args, extra_env=None):
    env = {**rm_env["env"]}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True, text=True, env=env,
    )


def _manifest_path(rm_env, slug="ws-slug"):
    return rm_env["state_dir"] / "rmgroup" / "worktrees" / slug / "manifest.json"


class TestCampRmSuccess:
    def test_rm_clean_workspace_exits_zero(self, rm_env):
        """camp rm <slug> on a clean workspace exits 0."""
        r = _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup")
        assert r.returncode == 0, (
            f"camp rm should exit 0 on a clean workspace.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_rm_removes_manifest(self, rm_env):
        """camp rm <slug> removes the central manifest."""
        manifest = _manifest_path(rm_env)
        assert manifest.is_file(), "manifest should exist before rm"

        _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup")

        assert not manifest.exists(), (
            f"manifest should be removed after camp rm.\n"
            f"manifest path: {manifest}"
        )

    def test_rm_name_flag_resolves_slug(self, rm_env):
        """camp rm --name <slug> uses the --name flag to resolve the slug."""
        r = _camp(rm_env, "rm", "--name", "ws-slug", "--group", "rmgroup")
        assert r.returncode == 0, (
            f"camp rm --name <slug> should exit 0.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_rm_announces_removed_slug(self, rm_env):
        """camp rm output names the slug that was removed."""
        r = _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup")
        combined = r.stdout + r.stderr
        assert "ws-slug" in combined, (
            f"camp rm output should mention the slug.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )


class TestCampRmDirtyBlock:
    def _make_dirty(self, rm_env):
        """Write an uncommitted file into the repo_a worktree."""
        ws = rm_env["ws_dir"]
        worktree_a = ws / "repo_a"
        if worktree_a.is_dir():
            (worktree_a / "dirty.txt").write_text("uncommitted change\n")

    def test_dirty_worktree_blocked_without_force(self, rm_env):
        """camp rm without --force blocks on a dirty worktree (non-zero exit)."""
        self._make_dirty(rm_env)
        r = _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup")
        assert r.returncode != 0, (
            f"camp rm should exit non-zero when worktree is dirty.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_dirty_worktree_blocked_message_mentions_dirty(self, rm_env):
        """Dirty-block error message mentions 'dirty' or 'uncommitted'."""
        self._make_dirty(rm_env)
        r = _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup")
        combined = r.stdout + r.stderr
        assert (
            "dirty" in combined.lower()
            or "uncommitted" in combined.lower()
            or "force" in combined.lower()
        ), (
            f"dirty-block error should mention 'dirty', 'uncommitted', or 'force'.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_dirty_worktree_succeeds_with_force(self, rm_env):
        """camp rm --force succeeds even when the worktree is dirty."""
        self._make_dirty(rm_env)
        r = _camp(rm_env, "rm", "--force", "ws-slug", "--group", "rmgroup")
        assert r.returncode == 0, (
            f"camp rm --force should exit 0 on a dirty worktree.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_dirty_worktree_force_removes_manifest(self, rm_env):
        """camp rm --force removes the manifest even when the worktree was dirty."""
        self._make_dirty(rm_env)
        _camp(rm_env, "rm", "--force", "ws-slug", "--group", "rmgroup")
        assert not _manifest_path(rm_env).exists(), (
            "manifest should be removed after camp rm --force"
        )


class TestCampRmUnknownSlug:
    def test_unknown_slug_exits_nonzero(self, rm_env):
        """camp rm <unknown-slug> exits non-zero with a legible error."""
        r = _camp(rm_env, "rm", "no-such-slug", "--group", "rmgroup")
        assert r.returncode != 0, (
            f"camp rm on an unknown slug should exit non-zero.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_unknown_slug_error_is_legible(self, rm_env):
        """camp rm <unknown-slug> error message names the missing slug."""
        r = _camp(rm_env, "rm", "no-such-slug", "--group", "rmgroup")
        combined = r.stdout + r.stderr
        assert (
            "no-such-slug" in combined
            or "manifest" in combined.lower()
            or "not found" in combined.lower()
        ), (
            f"unknown-slug error should name the slug or explain what's missing.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )


class TestCampRmInvokesReconcileBreak:
    def test_rm_reaches_reconcile_break_confinement(self, rm_env):
        """camp rm reachability: reconcile_break's confinement is exercised through
        the camp rm CLI path. A manifest with an old-layout path triggers a legible
        error, confirming the real reconcile_break is called (not the stub).
        """
        from manifest import write_central_manifest, manifest_path_for

        env = {"CAMP_STATE_DIR": str(rm_env["state_dir"])}
        mpath = manifest_path_for("rmgroup", "ws-slug", env=env)

        # Overwrite manifest with an old-layout worktree_path to trigger LegacyLayoutError
        # (which only reconcile_break checks — the stub never reads the manifest)
        old_layout_path = str(rm_env["repo_a"] / ".claude" / "worktrees" / "ws-slug" / "repo_a")
        data = json.loads(mpath.read_text())
        data["members"][0]["worktree_path"] = old_layout_path
        write_central_manifest(mpath, data)

        r = _camp(rm_env, "rm", "ws-slug", "--group", "rmgroup")
        combined = r.stdout + r.stderr
        assert r.returncode != 0, "old-layout path should trigger a non-zero exit"
        assert (
            "legacy" in combined.lower()
            or "retired" in combined.lower()
            or "manually" in combined.lower()
        ), (
            f"camp rm should surface the LegacyLayoutError from reconcile_break.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
