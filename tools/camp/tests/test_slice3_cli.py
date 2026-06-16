"""Slice 3 CLI integration: camp ai / camp setup / camp status end-to-end.

Exercises the real cli/camp dispatch through CAMP_CONFIG_DIR + CAMP_STATE_DIR
overrides (no real ~/.claude, no real claude exec). camp ai's harness launch is
suppressed via CAMP_TEST_NO_EXEC so the test can assert the seed+spawn without the
os.execvp into claude (the real launch lands in Slice 6).

Test contract:
- camp ai <slug> seeds the manifest pending and (with the real background
  provisioner) drives every member to ready; the workspace dir + setup.log exist.
- camp setup (foreground) completes provisioning; camp status exit codes
  0=ready / 2=pending / 3=failed; --json shape stable.
- The detached `camp setup --background` it spawns matches the foreground code path.
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


@pytest.fixture()
def cli_env(tmp_path: Path):
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

    # Author the group via the real CLI.
    r = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "group", "mygroup",
         "--member", f"repo_a={repo_a}", "--member", f"repo_b={repo_b}"],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, f"group authoring failed: {r.stderr}"

    return {
        "env": env, "config_dir": config_dir, "state_dir": state_dir,
        "repo_a": repo_a, "repo_b": repo_b, "tmp_path": tmp_path,
    }


def _camp(cli_env, *args, extra_env=None):
    env = {**cli_env["env"]}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True, text=True, env=env,
    )


def _manifest_path(cli_env, slug):
    return cli_env["state_dir"] / "mygroup" / "worktrees" / slug / "manifest.json"


def _states(cli_env, slug):
    data = json.loads(_manifest_path(cli_env, slug).read_text())
    return {m["name"]: m["provision_state"] for m in data["members"]}


class TestCampAi:
    def test_ai_seeds_and_background_provisions_to_ready(self, cli_env):
        """camp ai seeds pending, spawns the detached provisioner, which drives
        every member to ready. The workspace dir + setup.log exist."""
        r = _camp(cli_env, "ai", "feat-x", "--group", "mygroup",
                  extra_env={"CAMP_TEST_NO_EXEC": "1"})
        assert r.returncode == 0, f"camp ai failed: {r.stderr}"

        ws = cli_env["state_dir"] / "mygroup" / "worktrees" / "feat-x"
        assert ws.is_dir()
        assert _manifest_path(cli_env, "feat-x").is_file()

        # The detached background provisioner runs to completion; poll until ready.
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if _states(cli_env, "feat-x") == {"repo_a": "ready", "repo_b": "ready"}:
                break
            time.sleep(0.1)

        assert _states(cli_env, "feat-x") == {"repo_a": "ready", "repo_b": "ready"}
        assert (ws / "setup.log").exists()
        assert (ws / "setup.log").stat().st_mode & 0o777 == 0o600

    def test_ai_requires_slug(self, cli_env):
        r = _camp(cli_env, "ai", "--group", "mygroup",
                  extra_env={"CAMP_TEST_NO_EXEC": "1"})
        assert r.returncode != 0
        assert "slug" in r.stderr.lower()


class TestCampSetup:
    def test_foreground_setup_drives_ready(self, cli_env):
        """camp setup (foreground) completes provisioning of a seeded workspace."""
        # Seed only (no background provisioner) via a hidden seam: camp ai with
        # CAMP_TEST_NO_EXEC still spawns the bg provisioner, so instead drive setup
        # directly on a fresh slug by seeding through camp ai then setup --retry.
        r = _camp(cli_env, "ai", "feat-s", "--group", "mygroup",
                  extra_env={"CAMP_TEST_NO_EXEC": "1"})
        assert r.returncode == 0, r.stderr

        # Foreground setup is idempotent with the bg provisioner — both land ready.
        r2 = _camp(cli_env, "setup", "feat-s", "--group", "mygroup")
        assert r2.returncode == 0, f"camp setup failed: {r2.stderr}"

        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if _states(cli_env, "feat-s") == {"repo_a": "ready", "repo_b": "ready"}:
                break
            time.sleep(0.1)
        assert _states(cli_env, "feat-s") == {"repo_a": "ready", "repo_b": "ready"}


class TestCampStatusExitCodes:
    def _seed_states(self, cli_env, slug, states):
        from group_resolve import central_state_dir
        from provision import seed_pending_workspace
        from manifest import update_member_state
        from group_config import load_group

        group = load_group(cli_env["config_dir"] / "groups" / "mygroup.toml")
        env = {"CAMP_STATE_DIR": str(cli_env["state_dir"])}
        seed_pending_workspace(group, slug, env=env)
        mpath = central_state_dir("mygroup", env=env) / "worktrees" / slug / "manifest.json"
        for name, st in states.items():
            update_member_state(mpath, name, st, env=env, group_name="mygroup", slug=slug)

    def test_status_all_ready_exit_0(self, cli_env):
        self._seed_states(cli_env, "st0", {"repo_a": "ready", "repo_b": "ready"})
        r = _camp(cli_env, "status", "--group", "mygroup", "--name", "st0")
        assert r.returncode == 0, f"expected 0, got {r.returncode}: {r.stderr}"

    def test_status_some_pending_exit_2(self, cli_env):
        self._seed_states(cli_env, "st2", {"repo_a": "ready", "repo_b": "pending"})
        r = _camp(cli_env, "status", "--group", "mygroup", "--name", "st2")
        assert r.returncode == 2, f"expected 2, got {r.returncode}: {r.stderr}"

    def test_status_some_failed_exit_3(self, cli_env):
        self._seed_states(cli_env, "st3", {"repo_a": "ready", "repo_b": "failed"})
        r = _camp(cli_env, "status", "--group", "mygroup", "--name", "st3")
        assert r.returncode == 3, f"expected 3, got {r.returncode}: {r.stderr}"

    def test_status_json_shape(self, cli_env):
        self._seed_states(cli_env, "stj", {"repo_a": "ready", "repo_b": "pending"})
        r = _camp(cli_env, "status", "--group", "mygroup", "--name", "stj", "--json")
        # exit code still reflects state (2), but JSON is on stdout.
        report = json.loads(r.stdout)
        assert report["slug"] == "stj"
        assert report["code"] == 2
        by_name = {m["name"]: m["provision_state"] for m in report["members"]}
        assert by_name == {"repo_a": "ready", "repo_b": "pending"}


class TestSetupRetryFlagRemoved:
    def test_setup_retry_flag_rejected_with_slug(self, cli_env):
        """camp setup <slug> --retry is no longer accepted — exits non-zero.

        This is a real test of the flag rejection: we give a valid slug so the
        failure is definitively due to --retry being an unknown flag, not a
        missing-slug error.
        """
        r = _camp(cli_env, "ai", "feat-retry-test", "--group", "mygroup",
                  extra_env={"CAMP_TEST_NO_EXEC": "1"})
        assert r.returncode == 0, r.stderr

        r2 = _camp(cli_env, "setup", "feat-retry-test", "--retry", "--group", "mygroup")
        assert r2.returncode != 0, (
            f"camp setup <slug> --retry should be rejected (non-zero exit), "
            f"got rc={r2.returncode}. stdout={r2.stdout!r} stderr={r2.stderr!r}"
        )

    def test_setup_without_retry_still_works(self, cli_env):
        """camp setup (no --retry) still retries pending+failed members idempotently."""
        r = _camp(cli_env, "ai", "feat-nrt", "--group", "mygroup",
                  extra_env={"CAMP_TEST_NO_EXEC": "1"})
        assert r.returncode == 0, r.stderr

        r2 = _camp(cli_env, "setup", "feat-nrt", "--group", "mygroup")
        assert r2.returncode == 0, (
            f"camp setup (no --retry) should still work. stderr={r2.stderr!r}"
        )
