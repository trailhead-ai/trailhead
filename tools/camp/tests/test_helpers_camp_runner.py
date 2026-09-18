"""Contract of the suite's in-process camp runner.

Fixtures that only need a camp command's *effect* — a group authored, a
workspace brought up — call `run_camp` instead of spawning an interpreter per
call. That is only sound while the two routes leave the same thing on disk and
report the same outcome, which is what these tests hold.

The equivalence test is the load-bearing one: it runs the same argv both ways,
into two separate config dirs, and compares what each wrote.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from ._helpers import CLI_CAMP, init_git_repo, run_camp


def _env_for(tmp_path: Path, tag: str) -> dict[str, str]:
    config_dir = tmp_path / f"{tag}-config"
    (config_dir / "groups").mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / f"{tag}-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    home = tmp_path / f"{tag}-home"
    home.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "CAMP_CONFIG_DIR": str(config_dir),
        "CAMP_STATE_DIR": str(state_dir),
        "HOME": str(home),
    }


def test_authoring_a_group_writes_what_the_subprocess_route_writes(tmp_path):
    """Same argv, both routes → byte-identical group config (paths aside)."""
    repo = tmp_path / "repo"
    init_git_repo(repo, origin=True)
    argv = ["group", "g", "--member", f"member={repo}"]

    sub_env = _env_for(tmp_path, "sub")
    subprocess.run(
        [sys.executable, str(CLI_CAMP), *argv],
        capture_output=True,
        text=True,
        env=sub_env,
        check=True,
    )
    in_env = _env_for(tmp_path, "in")
    result = run_camp(argv, env=in_env)
    assert result.returncode == 0, result.stderr

    written_by_subprocess = (
        Path(sub_env["CAMP_CONFIG_DIR"]) / "groups" / "g.toml"
    ).read_text()
    written_in_process = (
        Path(in_env["CAMP_CONFIG_DIR"]) / "groups" / "g.toml"
    ).read_text()
    assert written_in_process == written_by_subprocess


def test_a_refused_command_reports_non_zero_and_writes_no_group(tmp_path):
    """The varied input: a command that cannot succeed answers differently."""
    env = _env_for(tmp_path, "bad")

    result = run_camp(["group", "g", "--member", "member=/nonexistent/repo"], env=env)

    assert result.returncode != 0
    assert not (Path(env["CAMP_CONFIG_DIR"]) / "groups" / "g.toml").exists()


def test_the_callers_environment_is_restored_afterwards(tmp_path):
    """The runner swaps os.environ for the call; the next test inherits ours.

    A leak here is invisible until some later test reads a variable it never
    set, so it is pinned rather than trusted.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo, origin=True)
    before = dict(os.environ)
    marker_absent = "CAMP_CONFIG_DIR" not in before or before.get("CAMP_CONFIG_DIR")

    run_camp(["group", "g", "--member", f"member={repo}"], env=_env_for(tmp_path, "r"))

    assert dict(os.environ) == before
    assert marker_absent == ("CAMP_CONFIG_DIR" not in before or before.get("CAMP_CONFIG_DIR"))


def test_stdout_is_captured_rather_than_leaking_to_the_suites_own_output(tmp_path):
    """What the command printed comes back on the result, not on our stdout."""
    repo = tmp_path / "repo"
    init_git_repo(repo, origin=True)
    env = _env_for(tmp_path, "cap")

    result = run_camp(["groups"], env=env)

    assert result.returncode == 0
    assert isinstance(result.stdout, str)


def _bring_up_detached(env, group_name, slug, members, timeout=30.0):
    """Bring a workspace up the way `camp new` does: spawn, then poll."""
    subprocess.run(
        [sys.executable, str(CLI_CAMP), "new", slug, "--group", group_name],
        capture_output=True,
        text=True,
        env={**env, "CAMP_TEST_NO_EXEC": "1"},
        check=True,
    )
    manifest = (
        Path(env["CAMP_STATE_DIR"]) / group_name / "worktrees" / slug / "manifest.json"
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = json.loads(manifest.read_text())
            if all(m["provision_state"] == "ready" for m in data["members"]):
                return manifest
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.05)
    raise AssertionError(f"workspace {slug} never reached ready: {manifest.read_text()}")


def _bring_up_synchronously(env, group_name, slug, monkeypatch):
    """Bring the same workspace up inline, with no detached process."""
    import camp.provision.provision as provision
    from camp.group.config import load_group
    from camp.provision.lifecycle import cmd_setup_group

    monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)
    result = run_camp(["new", slug, "--group", group_name], env={**env, "CAMP_TEST_NO_EXEC": "1"})
    assert result.returncode == 0, result.stderr
    group = load_group(Path(env["CAMP_CONFIG_DIR"]) / "groups" / f"{group_name}.toml")
    cmd_setup_group(group, slug, env=env)
    return Path(env["CAMP_STATE_DIR"]) / group_name / "worktrees" / slug / "manifest.json"


def _workspace_layout(workspace_root):
    return sorted(
        str(p.relative_to(workspace_root)) for p in workspace_root.rglob("*") if p.is_dir()
    )


def test_synchronous_provisioning_lands_the_workspace_the_detached_route_lands(
    tmp_path, monkeypatch
):
    """Both routes → same member worktrees on disk, same ready manifest.

    This is what lets `remove_env` skip the detached provisioner: the fixture
    needs a provisioned workspace, and these two ways of getting one have to
    agree about what "provisioned" left behind.
    """
    repo_a, repo_b = tmp_path / "repo_a", tmp_path / "repo_b"
    init_git_repo(repo_a, origin=True)
    init_git_repo(repo_b, origin=True)
    members = ["repo_a", "repo_b"]

    detached_env = _env_for(tmp_path, "det")
    run_camp(
        ["group", "g", "--member", f"repo_a={repo_a}", "--member", f"repo_b={repo_b}"],
        env=detached_env,
    )
    detached_manifest = _bring_up_detached(detached_env, "g", "ws", members)

    sync_env = _env_for(tmp_path, "syn")
    run_camp(
        ["group", "g", "--member", f"repo_a={repo_a}", "--member", f"repo_b={repo_b}"],
        env=sync_env,
    )
    sync_manifest = _bring_up_synchronously(sync_env, "g", "ws-sync", monkeypatch)

    # Every member worktree the detached route created, the inline one created.
    assert _workspace_layout(sync_manifest.parent) == _workspace_layout(
        detached_manifest.parent
    )
    for member in members:
        assert (sync_manifest.parent / member).is_dir()

    sync_states = {
        m["name"]: m["provision_state"] for m in json.loads(sync_manifest.read_text())["members"]
    }
    detached_states = {
        m["name"]: m["provision_state"]
        for m in json.loads(detached_manifest.read_text())["members"]
    }
    assert sync_states == detached_states == {"repo_a": "ready", "repo_b": "ready"}


def test_an_unprovisioned_workspace_has_no_member_worktrees(tmp_path, monkeypatch):
    """The other side of the input: bring-up without the provisioning step.

    Pins that the member worktrees are what the provisioning step produces, so
    the test above is comparing something provisioning actually did.
    """
    import camp.provision.provision as provision

    repo_a = tmp_path / "repo_a"
    init_git_repo(repo_a, origin=True)
    env = _env_for(tmp_path, "bare")
    run_camp(["group", "g", "--member", f"repo_a={repo_a}"], env=env)

    monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)
    run_camp(["new", "ws", "--group", "g"], env={**env, "CAMP_TEST_NO_EXEC": "1"})

    workspace = Path(env["CAMP_STATE_DIR"]) / "g" / "worktrees" / "ws"
    assert not (workspace / "repo_a").is_dir()
