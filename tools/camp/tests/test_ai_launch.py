"""Tests for Slice 6: camp ai session attach + refuse-concurrent + launch seam.

Exercises _cmd_ai_group_cli in-process: the launch seam (harness_launch.launch)
and the session lock are stubbed so no real claude execs and no real lock-holder
exists. Asserts the lock lifecycle + new-vs-resume choice around the stubbed seam.

Test contract (Slice 6, camp ai portion):
- first camp ai (no workspace) → `new` template launch invoked; lock written.
- second camp ai (existing workspace, no live lock) → `resume` template launch.
- camp ai with a lock held by a LIVE PID → refuses, non-zero, names workspace +
  PID + timestamp; launcher NOT invoked.
- stale lock (dead PID) → reclaimed, launch proceeds.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_SCRIPTS_DIR = _PLUGIN_DIR / "scripts"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _load_cli_module():
    """Import cli/camp (extensionless) as a module for in-process dispatch tests."""
    spec = importlib.util.spec_from_loader(
        "camp_cli", importlib.machinery.SourceFileLoader("camp_cli", str(_CLI_CAMP))
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def camp_cli():
    return _load_cli_module()


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (path / "README.md").write_text("# t\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "i", "--no-gpg-sign"],
                   check=True, capture_output=True)


@pytest.fixture()
def group_env(tmp_path):
    repo_a = tmp_path / "repo_a"
    _init_git_repo(repo_a)
    group = {
        "group": {"name": "g"},
        "members": [{"name": "repo_a", "repo_root": str(repo_a),
                     "bootstrap": [], "base": "origin/main"}],
        "branch_pattern": "worktree-{slug}",
    }
    env = {"CAMP_STATE_DIR": str(tmp_path / "state")}
    return {"group": group, "env": env, "tmp_path": tmp_path}


def _workspace_dir(env, slug):
    from group_resolve import central_state_dir
    return central_state_dir("g", env=env) / "worktrees" / slug


@pytest.fixture(autouse=True)
def _stub_spawn(monkeypatch):
    """Never spawn a real detached provisioner in these tests."""
    import provision
    monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)


class TestNewVsResume:
    def test_first_ai_uses_new_template_and_writes_lock(self, camp_cli, group_env, monkeypatch):
        import harness_launch
        import session_lock

        calls = []
        monkeypatch.setattr(
            harness_launch, "launch",
            lambda group, slug, ws, *, is_resume, profile=None: calls.append(is_resume),
        )

        g = group_env
        camp_cli._cmd_ai_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)

        assert calls == [False], "first launch should use the new template"
        lock = session_lock.lock_path_for(_workspace_dir(g["env"], "feat-x"))
        data = json.loads(lock.read_text())
        assert data["pid"] == os.getpid()
        assert "started_at" in data

    def test_second_ai_uses_resume_template(self, camp_cli, group_env, monkeypatch):
        import harness_launch
        import session_lock

        g = group_env
        # First launch creates the workspace; release the lock so the second can run.
        monkeypatch.setattr(harness_launch, "launch", lambda *a, **k: None)
        camp_cli._cmd_ai_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        session_lock.release_session_lock(_workspace_dir(g["env"], "feat-x"))

        calls = []
        monkeypatch.setattr(
            harness_launch, "launch",
            lambda group, slug, ws, *, is_resume, profile=None: calls.append(is_resume),
        )
        camp_cli._cmd_ai_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        assert calls == [True], "existing workspace should resume"


class TestRefuseConcurrent:
    def test_live_lock_refuses_and_does_not_launch(self, camp_cli, group_env, monkeypatch):
        import harness_launch
        import session_lock

        g = group_env
        ws = _workspace_dir(g["env"], "feat-x")
        ws.mkdir(parents=True)
        ts = datetime.now(timezone.utc).isoformat()
        session_lock.lock_path_for(ws).write_text(
            json.dumps({"pid": os.getpid(), "started_at": ts, "workspace": str(ws)})
        )

        launched = []
        monkeypatch.setattr(harness_launch, "launch",
                            lambda *a, **k: launched.append(True))

        with pytest.raises(SystemExit) as exc:
            camp_cli._cmd_ai_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        assert exc.value.code != 0
        assert launched == [], "launcher must NOT be invoked when refused"

    def test_live_lock_refusal_names_workspace_pid_timestamp(self, camp_cli, group_env,
                                                             monkeypatch, capsys):
        import harness_launch
        import session_lock

        g = group_env
        ws = _workspace_dir(g["env"], "feat-x")
        ws.mkdir(parents=True)
        ts = datetime.now(timezone.utc).isoformat()
        session_lock.lock_path_for(ws).write_text(
            json.dumps({"pid": os.getpid(), "started_at": ts, "workspace": str(ws)})
        )
        monkeypatch.setattr(harness_launch, "launch", lambda *a, **k: None)

        with pytest.raises(SystemExit):
            camp_cli._cmd_ai_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)

        err = capsys.readouterr().err
        assert str(ws) in err
        assert str(os.getpid()) in err
        assert ts in err

    def test_stale_dead_pid_lock_reclaimed_launch_proceeds(self, camp_cli, group_env, monkeypatch):
        import harness_launch
        import session_lock

        g = group_env
        ws = _workspace_dir(g["env"], "feat-x")
        ws.mkdir(parents=True)

        # Dead PID from a reaped child.
        child = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        dead = child.pid
        child.wait()
        if session_lock.is_pid_alive(dead):
            pytest.skip("PID recycled")

        session_lock.lock_path_for(ws).write_text(
            json.dumps({"pid": dead, "started_at": datetime.now(timezone.utc).isoformat(),
                        "workspace": str(ws)})
        )

        launched = []
        monkeypatch.setattr(harness_launch, "launch",
                            lambda *a, **k: launched.append(True))

        camp_cli._cmd_ai_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        assert launched == [True], "stale lock should be reclaimed and launch proceed"
        data = json.loads(session_lock.lock_path_for(ws).read_text())
        assert data["pid"] == os.getpid()


def _ws_settings(env, slug):
    return _workspace_dir(env, slug) / ".claude" / "settings.json"


def _posttooluse_commands(settings_path: Path) -> list[str]:
    data = json.loads(settings_path.read_text())
    return [
        h.get("command", "")
        for entry in data.get("hooks", {}).get("PostToolUse", [])
        for h in entry.get("hooks", [])
    ]


class TestBringUpInjectHook:
    """camp ai installs the PostToolUse → inject --drain hook ONLY for claude-hook."""

    def test_claude_hook_default_installs_posttooluse_hook(self, camp_cli, group_env, monkeypatch):
        import harness_launch

        monkeypatch.setattr(harness_launch, "launch", lambda *a, **k: None)

        g = group_env
        # No [harness] block → claude default → claude-hook strategy.
        camp_cli._cmd_ai_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)

        cmds = _posttooluse_commands(_ws_settings(g["env"], "feat-x"))
        assert any("inject --drain" in c for c in cmds), (
            f"Expected an inject --drain PostToolUse hook, got: {cmds}"
        )

    def test_stdout_strategy_does_not_install_posttooluse_hook(self, camp_cli, group_env,
                                                               monkeypatch):
        import harness_launch

        monkeypatch.setattr(harness_launch, "launch", lambda *a, **k: None)

        g = group_env
        g["group"]["harness"] = {"inject": "stdout"}
        camp_cli._cmd_ai_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)

        settings_path = _ws_settings(g["env"], "feat-x")
        if settings_path.is_file():
            cmds = _posttooluse_commands(settings_path)
            assert not any("inject --drain" in c for c in cmds), (
                f"stdout strategy must NOT install an inject hook, got: {cmds}"
            )

    def test_inject_hook_idempotent_on_reentry(self, camp_cli, group_env, monkeypatch):
        import harness_launch
        import session_lock

        monkeypatch.setattr(harness_launch, "launch", lambda *a, **k: None)

        g = group_env
        camp_cli._cmd_ai_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)
        session_lock.release_session_lock(_workspace_dir(g["env"], "feat-x"))
        camp_cli._cmd_ai_group_cli(["feat-x"], g["group"], g["env"], dry_run=False)

        cmds = _posttooluse_commands(_ws_settings(g["env"], "feat-x"))
        drain_cmds = [c for c in cmds if "inject --drain" in c]
        assert len(drain_cmds) == 1, f"Duplicate inject hook on re-entry: {cmds}"
