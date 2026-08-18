"""Tests for async provisioning, detached spawn, setup, status.

Test contract (all must RED before implementation, GREEN after):

1. Real survival (BLOCKING gate): a fork-then-os.execvp integration test
   against a no-op provisioner asserts the detached child completes + writes its
   sentinel AFTER the parent process image is replaced — proving the spawn helper
   actually detaches the child. This is the real proof the prover's probe stood in
   for; the probe (test_u1_detached_survival.py) is REPLACED by this test.

2. spawn_detached_provisioner: builds the `camp setup --background` argv, spawns
   with start_new_session=True, stdin=DEVNULL, std streams → setup.log (0o600).

3. camp ai seeds the manifest with each member provision_state=pending and spawns
   the detached provisioner (asserted via a spawn seam, not a real claude exec).

4. foreground camp setup: pending→ready on success; a failing member (incl. fetch
   timeout) → failed + reason, others ready; idempotent (leaves ready members
   untouched, retries pending/failed ones).

5. concurrency: camp setup started while the background provisioner holds
   .reconcile.lock serializes (no torn manifest, no double-add).

6. camp status exit codes: all-ready→0, some-pending→2, some-failed→3; --json shape.

Fixtures use real synthetic git repos in tmp_path + CAMP_STATE_DIR env injection;
no real claude exec, no ~/.claude.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

_VENV_PYTHON = sys.executable


# ---------------------------------------------------------------------------
# Helpers — synthetic git repos
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
    # Self-origin so the configured base `origin/main` resolves locally — a real
    # member always has a fetchable/resolvable base; without it the base-fetch
    # correctly fails the member (BUG 4 fix: no silent HEAD fallback).
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", str(path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "fetch", "origin", "--quiet"], check=True, capture_output=True
    )


def _make_group_config(name, members, *, branch_pattern="worktree-{slug}"):
    return {"group": {"name": name}, "members": members, "branch_pattern": branch_pattern}


def _camp_state_env(tmp_path: Path) -> dict[str, str]:
    state_root = tmp_path / "camp-state"
    state_root.mkdir(parents=True, exist_ok=True)
    return {"CAMP_STATE_DIR": str(state_root)}


def _member_wt(group_name, slug, member, env):
    from camp.group.resolve import central_state_dir

    return central_state_dir(group_name, env=env) / "worktrees" / slug / member


def _workspace_dir(group_name, slug, env):
    from camp.group.resolve import central_state_dir

    return central_state_dir(group_name, env=env) / "worktrees" / slug


@pytest.fixture()
def two_member_group(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    _init_git_repo(repo_a)
    _init_git_repo(repo_b)
    group = _make_group_config(
        "testgroup",
        [
            {"name": "repo_a", "repo_root": str(repo_a), "tasks": [], "base": "origin/main"},
            {"name": "repo_b", "repo_root": str(repo_b), "tasks": [], "base": "origin/main"},
        ],
    )
    env = _camp_state_env(tmp_path)
    return {"group": group, "repo_a": repo_a, "repo_b": repo_b, "env": env, "tmp_path": tmp_path}


# ===========================================================================
# Test 1: real survival (BLOCKING gate)
# ===========================================================================


class TestU1RealSurvival:
    """The detached child spawned by the real spawn helper survives the parent
    os.execvp and writes its sentinel AFTER the parent process image is gone."""

    def test_detached_provisioner_survives_parent_exec(self, tmp_path: Path) -> None:
        scratch = tmp_path / "u1"
        scratch.mkdir()
        sentinel = scratch / "sentinel.txt"
        logfile = scratch / "setup.log"

        # No-op provisioner script (stands in for `camp setup --background`):
        # sleeps so the parent definitely exec's first, then writes the sentinel.
        provisioner = scratch / "noop_provisioner.py"
        provisioner.write_text(
            textwrap.dedent(f"""\
            import time, os
            time.sleep(0.8)
            with open({str(sentinel)!r}, "w") as f:
                f.write(f"done pid={{os.getpid()}}\\n")
            print("provisioner completed", flush=True)
            """),
            encoding="utf-8",
        )

        # Parent: import the REAL spawn helper, spawn the detached child via it,
        # then immediately os.execvp over itself.
        parent = scratch / "parent.py"
        parent.write_text(
            textwrap.dedent(f"""\
            import sys, os
            sys.path.insert(0, {str(_PLUGIN_DIR)!r})
            from camp.provision.provision import spawn_detached_provisioner

            spawn_detached_provisioner(
                logfile_path={str(logfile)!r},
                _argv=[sys.executable, {str(provisioner)!r}],
            )
            os.execvp(sys.executable, [sys.executable, "-c", "pass"])
            """),
            encoding="utf-8",
        )

        result = subprocess.run([_VENV_PYTHON, str(parent)], capture_output=True, timeout=15)
        assert result.returncode == 0, (
            f"parent exited non-zero: {result.returncode}\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )

        deadline = time.monotonic() + 8.0
        while not sentinel.exists() and time.monotonic() < deadline:
            time.sleep(0.05)

        assert sentinel.exists(), (
            "detached child did not survive the parent os.execvp — "
            "async provisioning architecture is broken"
        )
        assert "done" in sentinel.read_text()

    def test_logfile_captures_child_output_after_exec(self, tmp_path: Path) -> None:
        scratch = tmp_path / "u1log"
        scratch.mkdir()
        sentinel = scratch / "sentinel.txt"
        logfile = scratch / "setup.log"

        provisioner = scratch / "noop_provisioner.py"
        provisioner.write_text(
            textwrap.dedent(f"""\
            import time, os
            time.sleep(0.5)
            with open({str(sentinel)!r}, "w") as f:
                f.write("done\\n")
            print("provisioner completed", flush=True)
            """),
            encoding="utf-8",
        )

        parent = scratch / "parent.py"
        parent.write_text(
            textwrap.dedent(f"""\
            import sys, os
            sys.path.insert(0, {str(_PLUGIN_DIR)!r})
            from camp.provision.provision import spawn_detached_provisioner
            spawn_detached_provisioner(
                logfile_path={str(logfile)!r},
                _argv=[sys.executable, {str(provisioner)!r}],
            )
            os.execvp(sys.executable, [sys.executable, "-c", "pass"])
            """),
            encoding="utf-8",
        )

        subprocess.run([_VENV_PYTHON, str(parent)], capture_output=True, timeout=15)

        deadline = time.monotonic() + 8.0
        while not sentinel.exists() and time.monotonic() < deadline:
            time.sleep(0.05)

        assert logfile.exists(), "setup.log never created"
        assert "provisioner completed" in logfile.read_text()

    def test_setup_log_written_0600(self, tmp_path: Path) -> None:
        """setup.log is created with mode 0o600 (security)."""
        from camp.provision.provision import spawn_detached_provisioner

        logfile = tmp_path / "setup.log"
        proc = spawn_detached_provisioner(
            logfile_path=str(logfile),
            _argv=[_VENV_PYTHON, "-c", "pass"],
        )
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        assert logfile.exists()
        mode = logfile.stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"


# ===========================================================================
# Test 2: spawn_detached_provisioner shape
# ===========================================================================


class TestSpawnDetached:
    def test_builds_camp_setup_background_argv_by_default(self, monkeypatch, tmp_path):
        """Without _argv, the spawn helper builds a `camp setup --background` argv
        scoped to the group/slug."""
        import camp.provision.provision as provision

        captured: dict[str, Any] = {}

        class _FakeProc:
            pid = 4242

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return _FakeProc()

        monkeypatch.setattr(provision.subprocess, "Popen", fake_popen)

        logfile = tmp_path / "setup.log"
        provision.spawn_detached_provisioner(
            group_name="testgroup",
            slug="feat-x",
            logfile_path=str(logfile),
        )

        argv = captured["argv"]
        assert "setup" in argv
        assert "--background" in argv
        assert "feat-x" in argv
        assert "testgroup" in argv

    def test_popen_uses_detach_flags(self, monkeypatch, tmp_path):
        """start_new_session=True + stdin=DEVNULL + stdout/stderr → logfile fd."""
        import camp.provision.provision as provision

        captured: dict[str, Any] = {}

        class _FakeProc:
            pid = 1

        def fake_popen(argv, **kwargs):
            captured["kwargs"] = kwargs
            return _FakeProc()

        monkeypatch.setattr(provision.subprocess, "Popen", fake_popen)

        logfile = tmp_path / "setup.log"
        provision.spawn_detached_provisioner(
            logfile_path=str(logfile),
            _argv=[_VENV_PYTHON, "-c", "pass"],
        )

        kwargs = captured["kwargs"]
        assert kwargs.get("start_new_session") is True
        assert kwargs.get("stdin") == subprocess.DEVNULL
        # stdout/stderr must be a writable file object (the logfile), not None.
        assert kwargs.get("stdout") is not None
        assert kwargs.get("stderr") is not None


# ===========================================================================
# Test 3: camp ai seeds pending + spawns detached provisioner
# ===========================================================================


class TestAiSeedAndSpawn:
    def test_ai_seeds_manifest_members_pending(self, two_member_group, monkeypatch):
        """camp ai (via bring_up) seeds the manifest with each member pending."""
        from camp.provision.provision import bring_up_workspace
        import camp.provision.provision as provision

        g = two_member_group

        # Stub the spawn so no real detached process is launched.
        monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)

        bring_up_workspace(g["group"], "feat-x", env=g["env"])

        from camp.group.manifest import read_central_manifest

        mpath = _workspace_dir("testgroup", "feat-x", g["env"]) / "manifest.json"
        data = read_central_manifest(mpath)
        states = {m["name"]: m["provision_state"] for m in data["members"]}
        assert states == {"repo_a": "pending", "repo_b": "pending"}

    def test_ai_creates_workspace_dir_synchronously(self, two_member_group, monkeypatch):
        import camp.provision.provision as provision

        monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)
        from camp.provision.provision import bring_up_workspace

        g = two_member_group
        bring_up_workspace(g["group"], "feat-y", env=g["env"])
        assert _workspace_dir("testgroup", "feat-y", g["env"]).is_dir()

    def test_ai_spawns_detached_provisioner(self, two_member_group, monkeypatch):
        import camp.provision.provision as provision
        from camp.provision.provision import bring_up_workspace

        g = two_member_group
        calls = []
        monkeypatch.setattr(
            provision,
            "spawn_detached_provisioner",
            lambda **kw: calls.append(kw),
        )

        bring_up_workspace(g["group"], "feat-z", env=g["env"])
        assert len(calls) == 1
        assert calls[0]["slug"] == "feat-z"
        # logfile points at setup.log inside the workspace dir.
        assert calls[0]["logfile_path"].endswith("setup.log")


# ===========================================================================
# claude pretrust wiring (gated, best-effort)
# ===========================================================================


def _read_trust(home: Path) -> dict:
    claude_json = home / ".claude.json"
    if not claude_json.is_file():
        return {}
    return json.loads(claude_json.read_text())


class TestPretrustWiring:
    """bring_up_workspace pre-seeds the claude trust flag for the launch cwd.

    Gated on profile.pretrust and profile.is_claude_launch(); best-effort (any
    exception is warned and non-fatal). env threads HOME through so the write
    lands under tmp, never the real ~/.claude.json
    (the harness CLI is not isolated by the trailhead env).
    """

    def _env(self, two_member_group):
        env = dict(two_member_group["env"])
        env["HOME"] = str(two_member_group["tmp_path"] / "home")
        (two_member_group["tmp_path"] / "home").mkdir(parents=True, exist_ok=True)
        return env

    def test_claude_default_writes_trust_under_tmp_home(self, two_member_group, monkeypatch):
        import camp.provision.provision as provision
        from camp.provision.provision import bring_up_workspace

        g = two_member_group
        env = self._env(g)
        monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)

        bring_up_workspace(g["group"], "feat-pt", env=env)

        ws_dir = _workspace_dir("testgroup", "feat-pt", env)
        trust = _read_trust(Path(env["HOME"]))
        key = str(ws_dir.resolve())
        assert trust["projects"][key]["hasTrustDialogAccepted"] is True

    def test_trust_targets_resolved_subpath_cwd(self, two_member_group, monkeypatch):
        import camp.provision.provision as provision
        from camp.provision.provision import bring_up_workspace

        g = two_member_group
        env = self._env(g)
        group = dict(g["group"])
        group["harness"] = {"cwd": "{workspace}/app"}
        monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)

        bring_up_workspace(group, "feat-sub", env=env)

        ws_dir = _workspace_dir("testgroup", "feat-sub", env)
        trust = _read_trust(Path(env["HOME"]))
        subpath = str((ws_dir / "app").resolve())
        assert trust["projects"][subpath]["hasTrustDialogAccepted"] is True
        assert str(ws_dir.resolve()) not in trust.get("projects", {})

    def test_pretrust_false_writes_nothing(self, two_member_group, monkeypatch):
        import camp.provision.provision as provision
        from camp.provision.provision import bring_up_workspace

        g = two_member_group
        env = self._env(g)
        group = dict(g["group"])
        group["harness"] = {"pretrust": False}
        monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)

        bring_up_workspace(group, "feat-off", env=env)

        assert _read_trust(Path(env["HOME"])) == {}
        # Bring-up otherwise unchanged: manifest seeded.
        from camp.group.manifest import read_central_manifest

        mpath = _workspace_dir("testgroup", "feat-off", env) / "manifest.json"
        assert read_central_manifest(mpath)["members"]

    def test_non_claude_launch_writes_nothing(self, two_member_group, monkeypatch):
        import camp.provision.provision as provision
        from camp.provision.provision import bring_up_workspace

        g = two_member_group
        env = self._env(g)
        group = dict(g["group"])
        group["harness"] = {"binary": "codex"}
        monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)

        bring_up_workspace(group, "feat-codex", env=env)

        assert _read_trust(Path(env["HOME"])) == {}

    def test_pretrust_exception_does_not_abort_bringup(self, two_member_group, monkeypatch):
        import camp.provision.provision as provision
        import camp.launch.claude_trust as claude_trust
        from camp.provision.provision import bring_up_workspace

        g = two_member_group
        env = self._env(g)
        spawned = []
        monkeypatch.setattr(
            provision,
            "spawn_detached_provisioner",
            lambda **kw: spawned.append(kw),
        )

        def boom(*a, **kw):
            raise RuntimeError("pretrust blew up")

        monkeypatch.setattr(claude_trust, "pretrust_workspace", boom)

        bring_up_workspace(g["group"], "feat-boom", env=env)

        # Best-effort invariant: manifest still seeded AND provisioner still spawned.
        from camp.group.manifest import read_central_manifest

        mpath = _workspace_dir("testgroup", "feat-boom", env) / "manifest.json"
        assert read_central_manifest(mpath)["members"]
        assert len(spawned) == 1

    def test_resume_path_trust_entry_present(self, two_member_group, monkeypatch):
        import camp.provision.provision as provision
        from camp.provision.provision import bring_up_workspace

        g = two_member_group
        env = self._env(g)
        monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)

        # First bring-up creates the workspace dir + trust entry.
        bring_up_workspace(g["group"], "feat-res", env=env)
        # Second bring-up over the existing ws_dir (resume path): entry asserted PRESENT.
        bring_up_workspace(g["group"], "feat-res", env=env)

        ws_dir = _workspace_dir("testgroup", "feat-res", env)
        trust = _read_trust(Path(env["HOME"]))
        key = str(ws_dir.resolve())
        assert trust["projects"][key]["hasTrustDialogAccepted"] is True

    def test_pretrust_false_does_not_abort_bringup(self, two_member_group, monkeypatch):
        """pretrust_workspace returning False (an abort path) is still non-fatal
        for bare `camp new` — bring-up warns and continues."""
        import camp.provision.provision as provision
        import camp.launch.claude_trust as claude_trust
        from camp.provision.provision import bring_up_workspace

        g = two_member_group
        env = self._env(g)
        spawned = []
        monkeypatch.setattr(
            provision,
            "spawn_detached_provisioner",
            lambda **kw: spawned.append(kw),
        )
        monkeypatch.setattr(claude_trust, "pretrust_workspace", lambda *a, **kw: False)

        bring_up_workspace(g["group"], "feat-false", env=env)

        from camp.group.manifest import read_central_manifest

        mpath = _workspace_dir("testgroup", "feat-false", env) / "manifest.json"
        assert read_central_manifest(mpath)["members"]
        assert len(spawned) == 1


# ===========================================================================
# Test 4: foreground camp setup — provisioning state machine
# ===========================================================================


class TestForegroundSetup:
    def test_setup_flips_pending_to_ready(self, two_member_group, monkeypatch):
        """A successful setup flips all pending members to ready."""
        import camp.provision.provision as provision

        monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)
        from camp.provision.provision import bring_up_workspace
        from camp.provision.lifecycle import cmd_setup_group

        g = two_member_group
        bring_up_workspace(g["group"], "feat-s", env=g["env"])

        cmd_setup_group(g["group"], "feat-s", env=g["env"])

        from camp.group.manifest import read_central_manifest

        mpath = _workspace_dir("testgroup", "feat-s", g["env"]) / "manifest.json"
        data = read_central_manifest(mpath)
        states = {m["name"]: m["provision_state"] for m in data["members"]}
        assert states == {"repo_a": "ready", "repo_b": "ready"}

        # The worktrees actually exist.
        assert _member_wt("testgroup", "feat-s", "repo_a", g["env"]).is_dir()
        assert _member_wt("testgroup", "feat-s", "repo_b", g["env"]).is_dir()

    def test_failing_member_marked_failed_others_ready(self, tmp_path, monkeypatch):
        """A member whose reconcile fails → failed + reason; others still ready."""
        import camp.provision.provision as provision

        monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)
        from camp.provision.provision import bring_up_workspace
        from camp.provision.lifecycle import cmd_setup_group

        repo_a = tmp_path / "repo_a"
        _init_git_repo(repo_a)
        env = _camp_state_env(tmp_path)
        # repo_b has a non-existent repo_root → worktree add fails.
        group = _make_group_config(
            "failg",
            [
                {
                    "name": "repo_a",
                    "repo_root": str(repo_a),
                    "tasks": [],
                    "base": "origin/main",
                },
                {
                    "name": "repo_b",
                    "repo_root": str(tmp_path / "nonexistent"),
                    "tasks": [],
                    "base": "origin/main",
                },
            ],
        )

        bring_up_workspace(group, "feat-f", env=env)
        cmd_setup_group(group, "feat-f", env=env)

        from camp.group.manifest import read_central_manifest
        from camp.group.resolve import central_state_dir

        mpath = central_state_dir("failg", env=env) / "worktrees" / "feat-f" / "manifest.json"
        data = read_central_manifest(mpath)
        by_name = {m["name"]: m for m in data["members"]}
        assert by_name["repo_a"]["provision_state"] == "ready"
        assert by_name["repo_b"]["provision_state"] == "failed"
        assert by_name["repo_b"].get("reason"), "failed member must carry a reason"

    def test_fetch_timeout_fails_that_member(self, two_member_group, monkeypatch):
        """A git fetch that exceeds the timeout fails that member rather than hanging."""
        import camp.provision.provision as provision
        import camp.provision.reconcile as reconcile

        g = two_member_group

        # Make the fetch time out for repo_b only.
        real_fetch = reconcile._fetch_base

        def slow_fetch(repo_root, base, *, timeout):
            if "repo_b" in str(repo_root):
                raise subprocess.TimeoutExpired(cmd=["git", "fetch"], timeout=timeout)
            return real_fetch(repo_root, base, timeout=timeout)

        monkeypatch.setattr(reconcile, "_fetch_base", slow_fetch)
        monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)
        from camp.provision.provision import bring_up_workspace
        from camp.provision.lifecycle import cmd_setup_group

        bring_up_workspace(g["group"], "feat-to", env=g["env"])
        cmd_setup_group(g["group"], "feat-to", env=g["env"])

        from camp.group.manifest import read_central_manifest

        mpath = _workspace_dir("testgroup", "feat-to", g["env"]) / "manifest.json"
        data = read_central_manifest(mpath)
        by_name = {m["name"]: m for m in data["members"]}
        assert by_name["repo_a"]["provision_state"] == "ready"
        assert by_name["repo_b"]["provision_state"] == "failed"
        assert "timeout" in by_name["repo_b"].get("reason", "").lower()

    def test_retry_reruns_only_non_ready(self, two_member_group, monkeypatch):
        """camp setup re-runs pending/failed members, leaves ready untouched."""
        import camp.provision.provision as provision

        monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)
        from camp.provision.provision import bring_up_workspace
        from camp.provision.lifecycle import cmd_setup_group

        g = two_member_group
        bring_up_workspace(g["group"], "feat-r", env=g["env"])

        # First setup → both ready.
        cmd_setup_group(g["group"], "feat-r", env=g["env"])

        # Track which members the per-member provision is invoked for on retry.
        processed: list[str] = []
        real_provision = provision.provision_member

        def tracking_provision(group, slug, member, *, completed=None, env):
            processed.append(member["name"])
            return real_provision(group, slug, member, completed=completed, env=env)

        monkeypatch.setattr(provision, "provision_member", tracking_provision)

        cmd_setup_group(g["group"], "feat-r", env=g["env"])

        # Both already ready → retry processes nothing.
        assert processed == [], f"retry re-ran ready members: {processed}"

    def test_setup_is_idempotent(self, two_member_group, monkeypatch):
        """Running setup twice does not crash or duplicate members."""
        import camp.provision.provision as provision

        monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)
        from camp.provision.provision import bring_up_workspace
        from camp.provision.lifecycle import cmd_setup_group

        g = two_member_group
        bring_up_workspace(g["group"], "feat-i", env=g["env"])
        cmd_setup_group(g["group"], "feat-i", env=g["env"])
        cmd_setup_group(g["group"], "feat-i", env=g["env"])

        from camp.group.manifest import read_central_manifest

        mpath = _workspace_dir("testgroup", "feat-i", g["env"]) / "manifest.json"
        data = read_central_manifest(mpath)
        assert len(data["members"]) == 2


# ===========================================================================
# Test 5: concurrency — serialize on .reconcile.lock
# ===========================================================================


class TestConcurrency:
    def test_seed_blocks_on_held_slug_lock(self, two_member_group, monkeypatch):
        """seed_pending_workspace must contend on the slug lock so a
        `camp new` seed cannot race a `camp remove` teardown into a ghost
        workspace. Proof: while the test holds the slug reconcile lock, a seed
        running in another thread blocks — it writes no manifest until released."""
        from camp.group.manifest import reconcile_lock, workspace_dir
        import camp.provision.provision as provision

        g = two_member_group
        slug = "feat-seedlock"
        ws_dir = workspace_dir("testgroup", slug, env=g["env"])
        mpath = ws_dir / "manifest.json"

        done = threading.Event()

        def run_seed():
            provision.seed_pending_workspace(g["group"], slug, env=g["env"])
            done.set()

        with reconcile_lock(ws_dir):
            t = threading.Thread(target=run_seed)
            t.start()
            # Give the seed thread time to start and block on the held lock.
            time.sleep(0.25)
            manifest_absent_while_locked = not mpath.exists()
            seed_blocked = not done.is_set()

        # Lock released → the seed proceeds.
        t.join(timeout=10)

        assert manifest_absent_while_locked, (
            "seed must not write the manifest while the slug lock is held"
        )
        assert seed_blocked, "seed must block on the held slug lock"
        assert done.is_set() and mpath.exists(), (
            "seed completes and writes the manifest once the lock releases"
        )

    def test_setup_serializes_on_reconcile_lock(self, two_member_group, monkeypatch):
        """A foreground setup started while a provisioner holds the lock
        serializes — no torn manifest, no double-add."""
        import camp.provision.provision as provision

        monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)
        from camp.provision.provision import bring_up_workspace
        from camp.provision.lifecycle import cmd_setup_group

        g = two_member_group
        bring_up_workspace(g["group"], "feat-c", env=g["env"])

        errors: list[Exception] = []

        def run_setup():
            try:
                cmd_setup_group(g["group"], "feat-c", env=g["env"])
            except Exception as e:  # pragma: no cover
                errors.append(e)

        t1 = threading.Thread(target=run_setup)
        t2 = threading.Thread(target=run_setup)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert not errors, f"concurrent setup raised: {errors}"

        from camp.group.manifest import read_central_manifest

        mpath = _workspace_dir("testgroup", "feat-c", g["env"]) / "manifest.json"
        data = read_central_manifest(mpath)
        # Manifest is intact (readable, two members, both ready) — not torn.
        assert len(data["members"]) == 2
        states = {m["name"]: m["provision_state"] for m in data["members"]}
        assert states == {"repo_a": "ready", "repo_b": "ready"}

    def test_status_read_during_write_never_torn(self, two_member_group, monkeypatch):
        """camp status reads the rename-replaced manifest atomically: a read
        concurrent with many writes is always valid JSON (never a partial)."""
        import camp.provision.provision as provision

        monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)
        from camp.provision.provision import bring_up_workspace
        from camp.group.manifest import (
            flip_member_state_unlocked,
            read_central_manifest,
            reconcile_lock,
        )

        g = two_member_group
        bring_up_workspace(g["group"], "feat-rt", env=g["env"])
        mpath = _workspace_dir("testgroup", "feat-rt", g["env"]) / "manifest.json"

        stop = threading.Event()
        torn: list[str] = []

        def writer():
            i = 0
            while not stop.is_set():
                state = "ready" if i % 2 else "pending"
                with reconcile_lock(mpath.parent):
                    flip_member_state_unlocked(mpath, "repo_a", state)
                i += 1

        def reader():
            for _ in range(200):
                try:
                    read_central_manifest(mpath)
                except Exception as e:
                    torn.append(str(e))

        w = threading.Thread(target=writer)
        r = threading.Thread(target=reader)
        w.start()
        r.start()
        r.join(timeout=30)
        stop.set()
        w.join(timeout=30)

        assert not torn, f"status read observed a torn manifest: {torn[:3]}"


# ===========================================================================
# Test 6: camp status exit codes + --json
# ===========================================================================


class TestStatusExitCodes:
    def _seed_states(self, group, slug, env, states: dict[str, str]):
        import camp.provision.provision as provision

        # Seed pending then flip to target states directly.
        from camp.group.manifest import flip_member_state_unlocked, reconcile_lock

        provision.seed_pending_workspace(group, slug, env=env)
        mpath = provision.workspace_dir(group["group"]["name"], slug, env=env) / "manifest.json"
        for name, state in states.items():
            with reconcile_lock(mpath.parent):
                flip_member_state_unlocked(mpath, name, state)

    def test_all_ready_exit_0(self, two_member_group):
        from camp.provision.lifecycle import provision_status_code

        g = two_member_group
        self._seed_states(g["group"], "s1", g["env"], {"repo_a": "ready", "repo_b": "ready"})
        code, _ = provision_status_code(g["group"], "s1", env=g["env"])
        assert code == 0

    def test_some_pending_exit_2(self, two_member_group):
        from camp.provision.lifecycle import provision_status_code

        g = two_member_group
        self._seed_states(g["group"], "s2", g["env"], {"repo_a": "ready", "repo_b": "pending"})
        code, _ = provision_status_code(g["group"], "s2", env=g["env"])
        assert code == 2

    def test_some_failed_exit_3(self, two_member_group):
        from camp.provision.lifecycle import provision_status_code

        g = two_member_group
        self._seed_states(g["group"], "s3", g["env"], {"repo_a": "ready", "repo_b": "failed"})
        code, _ = provision_status_code(g["group"], "s3", env=g["env"])
        assert code == 3

    def test_failed_takes_precedence_over_pending(self, two_member_group):
        """When both pending and failed exist, failed (3) wins."""
        from camp.provision.lifecycle import provision_status_code

        g = two_member_group
        self._seed_states(g["group"], "s4", g["env"], {"repo_a": "pending", "repo_b": "failed"})
        code, _ = provision_status_code(g["group"], "s4", env=g["env"])
        assert code == 3

    def test_json_shape_stable(self, two_member_group):
        from camp.provision.lifecycle import provision_status_code

        g = two_member_group
        self._seed_states(g["group"], "s5", g["env"], {"repo_a": "ready", "repo_b": "pending"})
        code, report = provision_status_code(g["group"], "s5", env=g["env"])
        assert report["slug"] == "s5"
        assert isinstance(report["members"], list)
        by_name = {m["name"]: m for m in report["members"]}
        assert by_name["repo_a"]["provision_state"] == "ready"
        assert by_name["repo_b"]["provision_state"] == "pending"
        assert report["code"] == code


# ===========================================================================
# Test 7: per-task surfacing in the status report + CLI output
# ===========================================================================


class TestStatusPerTaskSurfacing:
    """`camp status` surfaces each member's per-task state. Exit codes are
    unchanged — a ready member with a failed OPTIONAL task stays exit 0, the
    failed task merely becomes visible in the report and the CLI output."""

    def _seed(self, group, slug, env, members):
        """Seed a workspace, flipping each member to (state, tasks_map).

        members: {name: (provision_state, tasks_map)}.
        """
        import camp.provision.provision as provision
        from camp.group.manifest import flip_member_state_unlocked, reconcile_lock

        provision.seed_pending_workspace(group, slug, env=env)
        mpath = _workspace_dir(group["group"]["name"], slug, env) / "manifest.json"
        for name, (state, tasks) in members.items():
            with reconcile_lock(mpath.parent):
                flip_member_state_unlocked(mpath, name, state, tasks=tasks)
        return mpath

    def test_report_includes_tasks_map(self, two_member_group):
        """provision_status_code carries each member's manifest tasks map through
        to the report (both the text and --json paths read this dict)."""
        from camp.provision.lifecycle import provision_status_code

        g = two_member_group
        self._seed(
            g["group"],
            "rt",
            g["env"],
            {
                "repo_a": (
                    "ready",
                    {
                        "seed": {"state": "ok"},
                        "graphify": {"state": "failed", "reason": "boom"},
                    },
                ),
                "repo_b": ("ready", {}),
            },
        )
        code, report = provision_status_code(g["group"], "rt", env=g["env"])

        # A failed OPTIONAL task does NOT change the exit code — the member is ready.
        assert code == 0
        by_name = {m["name"]: m for m in report["members"]}
        assert by_name["repo_a"]["tasks"] == {
            "seed": {"state": "ok"},
            "graphify": {"state": "failed", "reason": "boom"},
        }
        # A member with no tasks reports an empty (never absent) map — stable shape.
        assert by_name["repo_b"]["tasks"] == {}

    def test_status_cli_text_lists_per_task_states(self, two_member_group, capsys):
        """The text CLI prints one indented `    <task>: <state>` sub-line per
        task, in manifest insertion order, under each member line, and exits 0
        when the only failing task is optional (member ready)."""
        from camp.cli.status import _cmd_status_group_cli

        g = two_member_group
        self._seed(
            g["group"],
            "cli",
            g["env"],
            {
                "repo_a": (
                    "ready",
                    {"seed": {"state": "ok"}, "graphify": {"state": "failed"}},
                ),
                "repo_b": ("ready", {}),
            },
        )

        with pytest.raises(SystemExit) as exc:
            _cmd_status_group_cli(["--name", "cli"], g["group"], g["env"], False)
        assert exc.value.code == 0

        lines = capsys.readouterr().out.splitlines()
        assert "  repo_a: ready" in lines
        assert "    seed: ok" in lines
        assert "    graphify: failed" in lines
        # Insertion order preserved and sub-lines sit under their member line.
        i_member = lines.index("  repo_a: ready")
        i_seed = lines.index("    seed: ok")
        i_graphify = lines.index("    graphify: failed")
        assert i_member < i_seed < i_graphify

    def test_status_cli_json_includes_tasks(self, two_member_group, capsys):
        """The --json path emits the same tasks map for structured consumers."""
        from camp.cli.status import _cmd_status_group_cli

        g = two_member_group
        self._seed(
            g["group"],
            "cj",
            g["env"],
            {
                "repo_a": ("ready", {"seed": {"state": "ok"}}),
                "repo_b": ("ready", {}),
            },
        )

        with pytest.raises(SystemExit) as exc:
            _cmd_status_group_cli(["--name", "cj", "--json"], g["group"], g["env"], False)
        assert exc.value.code == 0

        report = json.loads(capsys.readouterr().out)
        by_name = {m["name"]: m for m in report["members"]}
        assert by_name["repo_a"]["tasks"] == {"seed": {"state": "ok"}}
