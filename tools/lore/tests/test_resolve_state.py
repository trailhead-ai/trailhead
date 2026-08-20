"""Resolution-session state (``cli/resolve_state``) and the ``DRIFT_RESOLVING`` fence.

A vault stopped mid-rebase is *being resolved*, not merely dirty. Two things
follow, and both are covered here:

  - The resolution-session marker under ``state_dir("lore")/resolve/`` records an
    ownership token minted by ``lore resolve`` plus the invoking pid. The pid is
    **diagnostics only**: lore's CLI runs one subprocess per verb, so no pid is
    ever alive across two steps of a resolution. The sole liveness authority is
    real git rebase state — a marker whose vault is genuinely mid-rebase is LIVE
    even when its recorded pid is long dead, and a marker whose vault is not
    mid-rebase is stale whatever the pid says.
  - Every other write path is fenced off that vault: ``record create`` /
    ``record update`` / ``flush`` refuse and name ``lore resolve <vault>``;
    ``session candidate`` warns but still captures, because losing a finding to
    an in-progress rebase is worse than capturing it late.
"""

from __future__ import annotations

import json
import os

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import CLI_PATH, load_script, run_cli, write_vault_config


# ── fixtures ───────────────────────────────────────────────────────────────


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)


def _init_vault(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    for key, val in (("user.email", "t@e.st"), ("user.name", "Test"), ("commit.gpgsign", "false")):
        _git(path, "config", key, val)
    (path / "README.md").write_text("vault\n")
    (path / ".gitignore").write_text("*.lock\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "init")
    return path


def _strand_mid_rebase(vault: Path, tmp_path: Path) -> Path:
    """Leave ``vault`` genuinely stopped mid-rebase on a README conflict."""
    remote = tmp_path / f"{vault.name}-remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git(vault, "remote", "add", "origin", str(remote))
    _git(vault, "push", "-u", "origin", branch)

    other = tmp_path / f"{vault.name}-device-b"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True, capture_output=True)
    for key, val in (("user.email", "t@e.st"), ("user.name", "Test"), ("commit.gpgsign", "false")):
        _git(other, "config", key, val)
    (other / "README.md").write_text("edited on device B\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B edit")
    _git(other, "push", "origin", branch)

    (vault / "README.md").write_text("edited on device A\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "device A edit")
    _git(vault, "fetch", "origin")
    rc = _git(vault, "rebase", f"origin/{branch}")
    assert rc.returncode != 0, "the fixture must actually conflict"
    assert (vault / ".git" / "rebase-merge").exists(), "the vault must be stranded mid-rebase"
    return vault


@pytest.fixture
def resolve_state():
    return load_script("lore.cli.resolve_state")


@pytest.fixture
def common():
    return load_script("lore.cli.common")


def _dead_pid() -> int:
    """A pid that is genuinely dead — a real subprocess, run to completion."""
    proc = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True)
    pid = int(proc.stdout.strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    return pid


# ── marker lifecycle ───────────────────────────────────────────────────────


def test_marker_round_trip(tmp_path, resolve_state):
    vault = _init_vault(tmp_path / "vault")
    marker = resolve_state.begin_session(vault)

    assert marker["token"], "lore resolve mints an ownership token"
    assert marker["pid"] == os.getpid(), "the invoking pid is recorded (diagnostics only)"
    assert resolve_state.marker_path(vault).exists()

    assert resolve_state.read_marker(vault) == marker
    assert resolve_state.clear_marker(vault) is True
    assert resolve_state.read_marker(vault) is None
    assert resolve_state.clear_marker(vault) is False


def test_marker_lives_under_the_lore_state_dir(tmp_path, resolve_state, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    vault = _init_vault(tmp_path / "vault")
    assert resolve_state.marker_path(vault) == state / "lore" / "resolve" / "vault.json"


def test_marker_path_refuses_a_symlink_escape(tmp_path, resolve_state, monkeypatch):
    """The ``<vault-basename>`` path is confined, per vault.py's precedent."""
    from lore.vault import layers as layers_mod

    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    vault = _init_vault(tmp_path / "vault")
    root = state / "lore" / "resolve"
    root.mkdir(parents=True)
    outside = tmp_path / "elsewhere.json"
    outside.write_text("{}")
    (root / "vault.json").symlink_to(outside)

    with pytest.raises(layers_mod.LayerConfinementError):
        resolve_state.marker_path(vault)


# ── staleness: git rebase state is the sole authority ──────────────────────


def test_marker_is_stale_when_the_vault_is_not_mid_rebase(tmp_path, resolve_state):
    vault = _init_vault(tmp_path / "vault")
    resolve_state.begin_session(vault)  # minted by this very much alive process

    assert resolve_state.live_marker(vault) is None, "a live pid never makes a marker live"
    assert resolve_state.clear_if_stale(vault) is True
    assert resolve_state.read_marker(vault) is None


def test_marker_with_a_dead_pid_is_live_while_the_vault_is_mid_rebase(tmp_path, resolve_state):
    vault = _strand_mid_rebase(_init_vault(tmp_path / "vault"), tmp_path)
    marker = resolve_state.begin_session(vault)
    dead = _dead_pid()
    marker["pid"] = dead
    resolve_state.write_marker(vault, marker)

    live = resolve_state.live_marker(vault)
    assert live is not None, "a mid-rebase vault is LIVE regardless of the recorded pid"
    assert live["pid"] == dead
    assert resolve_state.clear_if_stale(vault) is False
    assert resolve_state.read_marker(vault) is not None


def test_marker_body_is_json(tmp_path, resolve_state):
    vault = _init_vault(tmp_path / "vault")
    marker = resolve_state.begin_session(vault)
    text = resolve_state.marker_path(vault).read_text(encoding="utf-8")
    assert json.loads(text) == marker
    assert text.endswith("\n")


# ── DRIFT_RESOLVING ────────────────────────────────────────────────────────


def test_mid_rebase_vault_drifts_as_resolving(tmp_path, common):
    vault = _strand_mid_rebase(_init_vault(tmp_path / "vault"), tmp_path)
    findings = common._vault_drift(vault)
    codes = [code for code, _ in findings]

    assert codes == [common.DRIFT_RESOLVING]
    assert common.DRIFT_UNCOMMITTED not in codes, "a conflicted tree is not mere dirt"
    assert common.DRIFT_RESOLVING not in common.DRIFT_SYNC_FIXABLE


def test_resolving_remedy_names_lore_resolve(tmp_path, common):
    init_mod = load_script("lore.cli.init")
    remedy = init_mod._drift_remedy("product", {common.DRIFT_RESOLVING})
    assert "lore resolve product" in remedy


def test_status_reports_the_resolving_vault(tmp_path):
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    vault = _strand_mid_rebase(_init_vault(tmp_path / "vault"), tmp_path)
    write_vault_config(config_home, [("default", "default", vault)])

    env = dict(os.environ)
    env.update({
        "XDG_CONFIG_HOME": str(config_home),
        "XDG_STATE_HOME": str(state_dir),
        "HOME": str(state_dir / "home"),
        "LORE_EMAIL": "tester@example.com",
    })
    r = subprocess.run([sys.executable, str(CLI_PATH), "status"],
                       capture_output=True, text=True, env=env)
    assert "lore resolve default" in r.stdout


# ── write-path fencing (real subprocess boundary) ──────────────────────────


def _tree(vault: Path) -> set:
    return {p.relative_to(vault) for p in vault.rglob("*") if ".git" not in p.parts}


def test_record_create_refuses_at_a_mid_rebase_vault(tmp_path):
    vault = _init_vault(tmp_path / "vault")
    state = tmp_path / "state"
    state.mkdir()
    _strand_mid_rebase(vault, tmp_path)
    before = _tree(vault)

    r = run_cli(["record", "create", "--kind", "lesson", "--title", "Fenced"],
                vault=vault, state_dir=state, stdin_text="body\n")
    assert r.returncode == 1
    assert "lore resolve vault" in r.stderr
    assert r.stdout.strip() == ""
    assert _tree(vault) == before, "a refused create writes nothing"


def test_record_update_refuses_at_a_mid_rebase_vault(tmp_path):
    vault = _init_vault(tmp_path / "vault")
    state = tmp_path / "state"
    state.mkdir()
    created = run_cli(["record", "create", "--kind", "lesson", "--title", "Existing"],
                      vault=vault, state_dir=state, stdin_text="body\n")
    assert created.returncode == 0, created.stderr
    record_id = created.stdout.strip()
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "record")
    _strand_mid_rebase(vault, tmp_path)

    r = run_cli(["record", "update", record_id, "--keyword", "fenced"],
                vault=vault, state_dir=state, stdin_text="")
    assert r.returncode == 1
    assert "lore resolve vault" in r.stderr
    shown = run_cli(["record", "show", record_id], vault=vault, state_dir=state)
    assert "fenced" not in shown.stdout, "a refused update writes nothing"


def test_flush_refuses_at_a_mid_rebase_vault(tmp_path):
    vault = _init_vault(tmp_path / "vault")
    state = tmp_path / "state"
    state.mkdir()
    env_extra = {"CLAUDE_CODE_SESSION_ID": "11111111-2222-3333-4444-555555555555"}
    cap = run_cli(["session", "candidate", "--kind", "decision", "--phase", "build"],
                  vault=vault, state_dir=state, stdin_text="a finding\n", env_extra=env_extra)
    assert cap.returncode == 0, cap.stderr
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "session")
    _strand_mid_rebase(vault, tmp_path)

    r = run_cli(["flush"], vault=vault, state_dir=state, env_extra=env_extra)
    assert r.returncode == 1
    assert "lore resolve vault" in r.stderr
    sidecar = json.loads((vault / "session" / "11111111-2222-3333-4444-555555555555.json").read_text())
    assert sidecar["status"] == "dirty", "a refused flush writes nothing"


def test_session_candidate_warns_but_still_captures(tmp_path):
    vault = _init_vault(tmp_path / "vault")
    state = tmp_path / "state"
    state.mkdir()
    _strand_mid_rebase(vault, tmp_path)

    r = run_cli(["session", "candidate", "--kind", "decision", "--phase", "build"],
                vault=vault, state_dir=state, stdin_text="captured anyway\n",
                env_extra={"CLAUDE_CODE_SESSION_ID": "66666666-7777-8888-9999-aaaaaaaaaaaa"})
    assert r.returncode == 0, r.stderr
    assert "lore resolve vault" in r.stderr
    body = (vault / "session" / "66666666-7777-8888-9999-aaaaaaaaaaaa.md").read_text()
    assert "captured anyway" in body, "a warned capture still lands"


def test_flush_all_refuses_before_flipping_anything(tmp_path):
    """The batch path refuses the whole batch, never half of it."""
    vault = _init_vault(tmp_path / "vault")
    state = tmp_path / "state"
    state.mkdir()
    keys = ["11111111-2222-3333-4444-555555555555", "22222222-3333-4444-5555-666666666666"]
    for key in keys:
        cap = run_cli(["session", "candidate", "--kind", "decision", "--phase", "build"],
                      vault=vault, state_dir=state, stdin_text="a finding\n",
                      env_extra={"CLAUDE_CODE_SESSION_ID": key})
        assert cap.returncode == 0, cap.stderr
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "sessions")
    _strand_mid_rebase(vault, tmp_path)

    r = run_cli(["flush", "all"], vault=vault, state_dir=state)
    assert r.returncode == 1
    assert "lore resolve vault" in r.stderr
    for key in keys:
        sidecar = json.loads((vault / "session" / f"{key}.json").read_text())
        assert sidecar["status"] == "dirty", "no session in the batch was flipped"
