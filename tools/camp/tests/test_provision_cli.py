"""CLI integration: camp new / camp setup / camp status end-to-end.

Exercises the real cli/camp dispatch through CAMP_CONFIG_DIR + CAMP_STATE_DIR
overrides (no real ~/.claude, no real claude exec). camp new's harness launch is
suppressed via CAMP_TEST_NO_EXEC so the test can assert the seed+spawn without the
os.execvp into claude.

Test contract:
- camp new <slug> seeds the manifest pending and (with the real background
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

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


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
    # Self-origin so the configured base `origin/main` resolves locally (a real
    # member always has a fetchable/resolvable base).
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", str(path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "fetch", "origin", "--quiet"], check=True, capture_output=True
    )


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
        [
            sys.executable,
            str(_CLI_CAMP),
            "group",
            "mygroup",
            "--member",
            f"repo_a={repo_a}",
            "--member",
            f"repo_b={repo_b}",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0, f"group authoring failed: {r.stderr}"

    return {
        "env": env,
        "config_dir": config_dir,
        "state_dir": state_dir,
        "repo_a": repo_a,
        "repo_b": repo_b,
        "tmp_path": tmp_path,
    }


def _camp(cli_env, *args, extra_env=None):
    env = {**cli_env["env"]}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _manifest_path(cli_env, slug):
    return cli_env["state_dir"] / "mygroup" / "worktrees" / slug / "manifest.json"


def _states(cli_env, slug):
    data = json.loads(_manifest_path(cli_env, slug).read_text())
    return {m["name"]: m["provision_state"] for m in data["members"]}


class TestCampNew:
    def test_new_seeds_and_background_provisions_to_ready(self, cli_env):
        """camp new seeds pending, spawns the detached provisioner, which drives
        every member to ready. The workspace dir + setup.log exist."""
        r = _camp(
            cli_env, "new", "feat-x", "--group", "mygroup", extra_env={"CAMP_TEST_NO_EXEC": "1"}
        )
        assert r.returncode == 0, f"camp new failed: {r.stderr}"

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

    def test_new_requires_slug(self, cli_env):
        r = _camp(cli_env, "new", "--group", "mygroup", extra_env={"CAMP_TEST_NO_EXEC": "1"})
        assert r.returncode != 0
        assert "slug" in r.stderr.lower()


class TestCampSetup:
    def test_foreground_setup_drives_ready(self, cli_env):
        """camp setup (foreground) completes provisioning of a seeded workspace."""
        # Seed only (no background provisioner) via a hidden seam: camp new with
        # CAMP_TEST_NO_EXEC still spawns the bg provisioner, so instead drive setup
        # directly on a fresh slug by seeding through camp new then setup --retry.
        r = _camp(
            cli_env, "new", "feat-s", "--group", "mygroup", extra_env={"CAMP_TEST_NO_EXEC": "1"}
        )
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
        from camp.group.resolve import central_state_dir
        from camp.provision.provision import seed_pending_workspace
        from camp.group.manifest import flip_member_state_unlocked, reconcile_lock
        from camp.group.config import load_group

        group = load_group(cli_env["config_dir"] / "groups" / "mygroup.toml")
        env = {"CAMP_STATE_DIR": str(cli_env["state_dir"])}
        seed_pending_workspace(group, slug, env=env)
        mpath = central_state_dir("mygroup", env=env) / "worktrees" / slug / "manifest.json"
        for name, st in states.items():
            with reconcile_lock(mpath.parent):
                flip_member_state_unlocked(mpath, name, st)

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


class TestCampStatusTwoFacts:
    """`camp status` reports boot-readiness and work-readiness as two
    independent facts, and derives its header + rollup from both while the
    process exit code continues to derive from boot-readiness alone."""

    def _seed_two_facts(self, cli_env, slug, members):
        """members: {name: {"provision_state": ..., "work_state": ..., "reason"?: ...}}"""
        from camp.group.resolve import central_state_dir
        from camp.provision.provision import seed_pending_workspace
        from camp.group.manifest import (
            read_central_manifest,
            write_central_manifest,
            reconcile_lock,
        )
        from camp.group.config import load_group

        group = load_group(cli_env["config_dir"] / "groups" / "mygroup.toml")
        env = {"CAMP_STATE_DIR": str(cli_env["state_dir"])}
        seed_pending_workspace(group, slug, env=env)
        mpath = central_state_dir("mygroup", env=env) / "worktrees" / slug / "manifest.json"
        with reconcile_lock(mpath.parent):
            data = read_central_manifest(mpath)
            for m in data["members"]:
                if m["name"] in members:
                    m.update(members[m["name"]])
            write_central_manifest(mpath, data)

    def test_boot_ready_work_pending_exits_0(self, cli_env):
        """The headline behavior change: a workspace whose members are
        boot-ready but still installing dependencies now exits 0, not 2."""
        self._seed_two_facts(
            cli_env,
            "twf0",
            {
                "repo_a": {"provision_state": "ready", "work_state": "ready"},
                "repo_b": {"provision_state": "ready", "work_state": "pending"},
            },
        )
        r = _camp(cli_env, "status", "--group", "mygroup", "--name", "twf0")
        assert r.returncode == 0, f"expected 0, got {r.returncode}: {r.stderr}"

    def test_exit_code_unaffected_by_failed_work_state(self, cli_env):
        """A failed work_state must never push the exit code to 3 while boot
        readiness is all-ready."""
        self._seed_two_facts(
            cli_env,
            "twf1",
            {
                "repo_a": {"provision_state": "ready", "work_state": "ready"},
                "repo_b": {"provision_state": "ready", "work_state": "failed"},
            },
        )
        r = _camp(cli_env, "status", "--group", "mygroup", "--name", "twf1")
        assert r.returncode == 0, f"expected 0, got {r.returncode}: {r.stderr}"

    def test_json_carries_work_rollup_independent_of_exit_code(self, cli_env):
        self._seed_two_facts(
            cli_env,
            "twf2",
            {
                "repo_a": {"provision_state": "ready", "work_state": "ready"},
                "repo_b": {"provision_state": "ready", "work_state": "failed"},
            },
        )
        r = _camp(cli_env, "status", "--group", "mygroup", "--name", "twf2", "--json")
        assert r.returncode == 0, f"expected 0, got {r.returncode}: {r.stderr}"
        report = json.loads(r.stdout)
        assert report["code"] == 0
        assert report["work_code"] == 3

    def test_header_all_ready(self, cli_env):
        self._seed_two_facts(
            cli_env,
            "twf3",
            {
                "repo_a": {"provision_state": "ready", "work_state": "ready"},
                "repo_b": {"provision_state": "ready", "work_state": "not-applicable"},
            },
        )
        r = _camp(cli_env, "status", "--group", "mygroup", "--name", "twf3")
        assert r.stdout.splitlines()[0] == "camp status: twf3 — ready"

    def test_header_mixed_boot_ready_work_pending(self, cli_env):
        self._seed_two_facts(
            cli_env,
            "twf4",
            {
                "repo_a": {"provision_state": "ready", "work_state": "ready"},
                "repo_b": {"provision_state": "ready", "work_state": "pending"},
            },
        )
        r = _camp(cli_env, "status", "--group", "mygroup", "--name", "twf4")
        assert r.stdout.splitlines()[0] == "camp status: twf4 — ready, work pending"

    def test_header_failed(self, cli_env):
        self._seed_two_facts(
            cli_env,
            "twf5",
            {
                "repo_a": {"provision_state": "failed", "work_state": "pending"},
                "repo_b": {"provision_state": "ready", "work_state": "ready"},
            },
        )
        r = _camp(cli_env, "status", "--group", "mygroup", "--name", "twf5")
        assert r.stdout.splitlines()[0] == "camp status: twf5 — failed"

    def test_per_member_line_carries_both_facts(self, cli_env):
        self._seed_two_facts(
            cli_env,
            "twf6",
            {
                "repo_a": {"provision_state": "ready", "work_state": "pending"},
                "repo_b": {"provision_state": "ready", "work_state": "ready"},
            },
        )
        r = _camp(cli_env, "status", "--group", "mygroup", "--name", "twf6")
        lines = r.stdout.splitlines()
        assert "  repo_a: ready / work: pending" in lines
        assert "  repo_b: ready / work: ready" in lines

    def test_per_task_sub_lines_keep_indentation_and_insertion_order(self, cli_env):
        """Regression guard: per-task sub-lines are documented as stable for
        agent parsing — two-space member indent, four-space task indent,
        manifest insertion order preserved."""
        self._seed_two_facts(
            cli_env,
            "twf7",
            {
                "repo_a": {
                    "provision_state": "ready",
                    "work_state": "ready",
                    "tasks": {
                        "seed": {"state": "ok"},
                        "graphify": {"state": "failed"},
                    },
                },
            },
        )
        r = _camp(cli_env, "status", "--group", "mygroup", "--name", "twf7")
        lines = r.stdout.splitlines()
        i_member = lines.index("  repo_a: ready / work: ready")
        i_seed = lines.index("    seed: ok")
        i_graphify = lines.index("    graphify: failed")
        assert i_member < i_seed < i_graphify

    def test_rollup_precedes_per_member_output(self, cli_env):
        self._seed_two_facts(
            cli_env,
            "twf8",
            {
                "repo_a": {"provision_state": "ready", "work_state": "ready"},
                "repo_b": {"provision_state": "ready", "work_state": "ready"},
            },
        )
        r = _camp(cli_env, "status", "--group", "mygroup", "--name", "twf8")
        lines = r.stdout.splitlines()
        i_header = lines.index("camp status: twf8 — ready")
        i_member = next(idx for idx, line in enumerate(lines) if line.startswith("  repo_a"))
        assert i_header < i_member


class TestCampSetupActivatePhaseRetry:
    """End-to-end (real subprocess CLI) coverage for camp setup's
    activate-phase retry: cleanup-first retry of a failed task, and the
    lock-scope guarantee that a concurrent camp rm is never blocked by it."""

    @pytest.fixture()
    def activate_cli_env(self, tmp_path: Path):
        config_dir = tmp_path / "camp-config"
        groups_dir = config_dir / "groups"
        groups_dir.mkdir(parents=True, exist_ok=True)
        state_dir = tmp_path / "camp-state"
        state_dir.mkdir(parents=True, exist_ok=True)

        repo_a = tmp_path / "repo_a"
        _init_git_repo(repo_a)

        group_name = "actgroup"
        cleanup_cmd = [
            sys.executable,
            "-c",
            "import sys; open(sys.argv[1], 'a').write('cleanup\\n')",
            "{worktree}/log.txt",
        ]
        step_cmd = [
            sys.executable,
            "-c",
            "import sys; open(sys.argv[1], 'a').write('step\\n')",
            "{worktree}/log.txt",
        ]

        def build_toml(step_cmd):
            return f"""\
[group]
name = "{group_name}"

[[members]]
name = "repo_a"
repo_root = "{repo_a}"
tasks = ["npm-ci"]

[tasks.npm-ci]
phase = "activate"
required = true
cleanup = {json.dumps(cleanup_cmd)}

[[tasks.npm-ci.steps]]
name = "install"
cmd = {json.dumps(step_cmd)}
"""

        (groups_dir / f"{group_name}.toml").write_text(build_toml(step_cmd))

        env = {**os.environ}
        env["CAMP_CONFIG_DIR"] = str(config_dir)
        env["CAMP_STATE_DIR"] = str(state_dir)

        return {
            "env": env,
            "group_name": group_name,
            "config_dir": config_dir,
            "state_dir": state_dir,
            "repo_a": repo_a,
            "tmp_path": tmp_path,
            "toml_path": groups_dir / f"{group_name}.toml",
            "build_toml": build_toml,
        }

    def _camp(self, activate_cli_env, *args, extra_env=None):
        env = {**activate_cli_env["env"]}
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(_CLI_CAMP), *args],
            capture_output=True,
            text=True,
            env=env,
        )

    def _mark_task_failed(self, activate_cli_env, slug):
        """Seed the manifest as if `camp activate` already ran and recorded a
        failed npm-ci task — the state camp setup's retry path repairs."""
        from camp.group.manifest import reconcile_lock
        from camp.group.resolve import central_state_dir

        env = {"CAMP_STATE_DIR": str(activate_cli_env["state_dir"])}
        mpath = central_state_dir(
            activate_cli_env["group_name"], env=env
        ) / "worktrees" / slug / "manifest.json"
        with reconcile_lock(mpath.parent):
            from camp.group.manifest import read_central_manifest, write_central_manifest

            data = read_central_manifest(mpath)
            for m in data["members"]:
                m["activated"] = True
                m["tasks"] = {"npm-ci": {"state": "failed"}}
            write_central_manifest(mpath, data)

    def _log_path(self, activate_cli_env, slug):
        from camp.group.resolve import central_state_dir

        env = {"CAMP_STATE_DIR": str(activate_cli_env["state_dir"])}
        return (
            central_state_dir(activate_cli_env["group_name"], env=env)
            / "worktrees"
            / slug
            / "repo_a"
            / "log.txt"
        )

    def test_setup_retries_failed_activate_task_end_to_end(self, activate_cli_env):
        """camp setup, run for real (no mocks), retries a failed activate-phase
        task and runs its cleanup step first."""
        slug = "act-e2e"
        r = self._camp(
            activate_cli_env,
            "new",
            slug,
            "--group",
            activate_cli_env["group_name"],
            extra_env={"CAMP_TEST_NO_EXEC": "1"},
        )
        assert r.returncode == 0, f"camp new failed: {r.stderr}"

        deadline = time.monotonic() + 20.0
        mpath = (
            activate_cli_env["state_dir"] / activate_cli_env["group_name"]
            / "worktrees" / slug / "manifest.json"
        )
        while time.monotonic() < deadline:
            data = json.loads(mpath.read_text())
            if data["members"][0]["provision_state"] == "ready":
                break
            time.sleep(0.1)
        assert data["members"][0]["provision_state"] == "ready"

        self._mark_task_failed(activate_cli_env, slug)

        r2 = self._camp(activate_cli_env, "setup", slug, "--group", activate_cli_env["group_name"])
        assert r2.returncode == 0, f"camp setup failed: {r2.stderr}"

        log_path = self._log_path(activate_cli_env, slug)
        assert log_path.read_text().splitlines() == ["cleanup", "step"], (
            "cleanup must run before the step on a retry of a failed task"
        )

        data = json.loads(mpath.read_text())
        assert data["members"][0]["tasks"]["npm-ci"]["state"] == "ok"

    def test_setup_activate_retry_does_not_block_concurrent_camp_rm(self, activate_cli_env):
        """The point of the slice, exercised through the real CLI: while camp
        setup is retrying a long activate-phase task, a concurrent camp rm
        on the SAME slug is not blocked for the task's duration."""
        slug = "act-rm-race"
        r = self._camp(
            activate_cli_env,
            "new",
            slug,
            "--group",
            activate_cli_env["group_name"],
            extra_env={"CAMP_TEST_NO_EXEC": "1"},
        )
        assert r.returncode == 0, f"camp new failed: {r.stderr}"

        mpath = (
            activate_cli_env["state_dir"] / activate_cli_env["group_name"]
            / "worktrees" / slug / "manifest.json"
        )
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            data = json.loads(mpath.read_text())
            if data["members"][0]["provision_state"] == "ready":
                break
            time.sleep(0.1)
        assert data["members"][0]["provision_state"] == "ready"

        # Swap the step for a long-running one so there is a real window to
        # race a concurrent `camp rm` against. Built directly into the TOML
        # (rather than patched in after the fact via string replace) so a
        # mismatched search string can't silently leave the step fast.
        slow_step_cmd = [
            sys.executable,
            "-c",
            # Deliberately far longer than any realistic camp-rm critical
            # section: proves rm returns without waiting this step out,
            # rather than merely returning faster than a tight number.
            "import sys, time; open(sys.argv[1], 'a').write('step\\n'); time.sleep(20)",
            "{worktree}/log.txt",
        ]
        toml_path = activate_cli_env["toml_path"]
        toml_text = activate_cli_env["build_toml"](slow_step_cmd)
        toml_path.write_text(toml_text)
        assert "time.sleep(20)" in toml_path.read_text(), (
            "the slow step must actually land in the written TOML"
        )

        self._mark_task_failed(activate_cli_env, slug)

        proc = subprocess.Popen(
            [sys.executable, str(_CLI_CAMP), "setup", slug, "--group", activate_cli_env["group_name"]],
            env=activate_cli_env["env"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            # Give the retry time to get past cleanup and into the long step.
            time.sleep(1.0)

            start = time.monotonic()
            rm = self._camp(
                activate_cli_env, "rm", slug, "--group", activate_cli_env["group_name"], "--force"
            )
            elapsed = time.monotonic() - start

            assert elapsed < 6.0, (
                f"camp rm took {elapsed:.2f}s while a concurrent activate-phase "
                "retry was running — the lock must not be held across the task subprocess"
            )
            assert rm.returncode == 0, f"camp rm failed: {rm.stderr}"
        finally:
            # The proof is already complete by this point; don't pay for the
            # remainder of the widened sleep in teardown.
            proc.kill()
            proc.wait(timeout=10)
