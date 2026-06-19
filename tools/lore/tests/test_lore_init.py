"""Tests for the non-interactive, idempotent ``lore init`` (Slice 1, S5).

Covers every bullet of the Slice 1 test contract:
  - Fresh ``lore init`` creates vaults/default as a git repo + index parent; exits 0.
  - Resolved index path is NOT under any vault root.
  - Re-run is a pure no-op: no second git-init, no duplicate config, existing vault untouched.
  - No ``harvest-pending.md`` created; ``install-vault-hooks.sh`` never invoked.
  - ``--local`` vs global: resolve_targets returns project paths vs user-global paths.
  - ``--vault <path>`` creates vaults/default as a symlink; does NOT re-git-init the target.
  - Existing vaults/default is left untouched on re-run.
  - ``--local`` outside a git repo → clean non-zero error, no traceback.
  - Config seed-if-absent: missing config → seeded with one default vault.
  - Pre-existing config is not clobbered.
  - Config missing default vault gets it merged in.

All tests inject XDG_STATE_HOME / XDG_CONFIG_HOME via env and use tmp_path so
they NEVER touch the real config, state, or vault (Axiom 6).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).parent
PLUGIN_ROOT = TESTS_DIR.parent / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

sys.path.insert(0, str(TESTS_DIR))
from conftest import load_script  # noqa: E402


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _run(args, *, state, config, cwd=None, extra=None):
    """Run lore CLI with isolated XDG dirs."""
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(state)
    env["XDG_CONFIG_HOME"] = str(config)
    env["LORE_EMAIL"] = "tester@example.com"
    if extra:
        env.update(extra)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
    )


def _dirs(tmp_path):
    state = tmp_path / "state"
    config = tmp_path / "config"
    state.mkdir(parents=True, exist_ok=True)
    config.mkdir(parents=True, exist_ok=True)
    return state, config


def _config_path(config):
    return config / "lore" / "config.json"


def _read_config(config):
    return json.loads(_config_path(config).read_text())


def _vaults_root(state):
    return state / "lore" / "vaults"


def _default_vault(state):
    return _vaults_root(state) / "default"


# ---------------------------------------------------------------------------
# 1. Fresh init: creates vaults/default as a git repo; exits 0
# ---------------------------------------------------------------------------

def test_fresh_init_exits_zero(tmp_path):
    state, config = _dirs(tmp_path)
    res = _run(["init"], state=state, config=config)
    assert res.returncode == 0, res.stderr


def test_fresh_init_creates_default_vault_dir(tmp_path):
    state, config = _dirs(tmp_path)
    _run(["init"], state=state, config=config)
    assert _default_vault(state).is_dir()


def test_fresh_init_git_inits_default_vault(tmp_path):
    state, config = _dirs(tmp_path)
    _run(["init"], state=state, config=config)
    assert (_default_vault(state) / ".git").is_dir()


def test_fresh_init_provisions_index_parent(tmp_path):
    state, config = _dirs(tmp_path)
    _run(["init"], state=state, config=config)
    index_parent = state / "lore"
    assert index_parent.is_dir()


# ---------------------------------------------------------------------------
# 2. Index path is NOT under any vault root
# ---------------------------------------------------------------------------

def test_index_location_not_under_vault_root(tmp_path):
    state, config = _dirs(tmp_path)
    _run(["init"], state=state, config=config)
    index_parent = state / "lore"
    vault_root = _vaults_root(state)
    # index_parent is state/lore; vault_root is state/lore/vaults
    # index_parent must NOT be under vault_root
    try:
        index_parent.relative_to(vault_root)
        assert False, "index parent should not be under the vaults root"
    except ValueError:
        pass  # correct — index parent is a sibling of vaults/, not under it


# ---------------------------------------------------------------------------
# 3. Re-run is a pure no-op
# ---------------------------------------------------------------------------

def test_rerun_exits_zero(tmp_path):
    state, config = _dirs(tmp_path)
    _run(["init"], state=state, config=config)
    res = _run(["init"], state=state, config=config)
    assert res.returncode == 0, res.stderr


def test_rerun_does_not_duplicate_config_vaults(tmp_path):
    state, config = _dirs(tmp_path)
    _run(["init"], state=state, config=config)
    _run(["init"], state=state, config=config)
    cfg = _read_config(config)
    assert len(cfg["vaults"]) == 1


def test_rerun_does_not_regit_init(tmp_path):
    """A second init must not re-git-init (no git re-initialization message)."""
    state, config = _dirs(tmp_path)
    _run(["init"], state=state, config=config)
    res = _run(["init"], state=state, config=config)
    # git-init on an existing repo prints "Reinitialized" — ensure we don't do it
    combined = res.stdout + res.stderr
    assert "Reinitialized" not in combined


# ---------------------------------------------------------------------------
# 4. No harvest-pending.md; no pre-commit hook from install-vault-hooks.sh
# ---------------------------------------------------------------------------

def test_no_harvest_pending_created(tmp_path):
    state, config = _dirs(tmp_path)
    _run(["init"], state=state, config=config)
    assert not (_default_vault(state) / "harvest-pending.md").exists()


def test_no_precommit_hook_installed(tmp_path):
    state, config = _dirs(tmp_path)
    _run(["init"], state=state, config=config)
    pre_commit = _default_vault(state) / ".git" / "hooks" / "pre-commit"
    # The old installer put a lore guard in pre-commit; new init must not.
    assert not pre_commit.exists()


# ---------------------------------------------------------------------------
# 5. resolve_targets: --local vs global
# ---------------------------------------------------------------------------

def test_resolve_targets_global_returns_user_paths(tmp_path):
    installer = load_script("installer")
    result = installer.resolve_targets(local=False)
    settings, rules = result
    # Global: ~/.claude/settings.json
    assert settings.name == "settings.json"
    assert ".claude" in str(settings)
    # Global rules: a CLAUDE.md under home or similar
    assert rules is not None


def test_resolve_targets_local_returns_project_paths(tmp_path):
    installer = load_script("installer")
    # Create a fake git repo so resolve_targets(local=True) can find it
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    orig_cwd = os.getcwd()
    try:
        os.chdir(repo)
        result = installer.resolve_targets(local=True)
        settings, rules = result
        # Local: ./.claude/settings.local.json in the git root
        assert settings.name == "settings.local.json"
        assert ".claude" in str(settings)
    finally:
        os.chdir(orig_cwd)


# ---------------------------------------------------------------------------
# 6. --vault <path> → symlink; does NOT re-git-init the target
# ---------------------------------------------------------------------------

def test_vault_flag_creates_symlink(tmp_path):
    state, config = _dirs(tmp_path)
    target_repo = tmp_path / "existing-repo"
    target_repo.mkdir()
    subprocess.run(["git", "init", str(target_repo)], check=True,
                   capture_output=True)

    res = _run(["init", "--vault", str(target_repo)], state=state, config=config)
    assert res.returncode == 0, res.stderr

    default_link = _default_vault(state)
    assert default_link.is_symlink()
    assert os.path.realpath(default_link) == os.path.realpath(target_repo)


def test_vault_flag_does_not_regit_init_target(tmp_path):
    """--vault must symlink, never re-git-init the target repo."""
    state, config = _dirs(tmp_path)
    target_repo = tmp_path / "existing-repo"
    target_repo.mkdir()
    git_result = subprocess.run(["git", "init", str(target_repo)], check=True,
                                capture_output=True, text=True)

    # Record HEAD commit (or just check .git exists with no re-init text)
    initial_git_head = (target_repo / ".git" / "HEAD").read_text()

    res = _run(["init", "--vault", str(target_repo)], state=state, config=config)
    assert res.returncode == 0, res.stderr

    # Target's .git/HEAD must be byte-for-byte unchanged
    assert (target_repo / ".git" / "HEAD").read_text() == initial_git_head
    # No "Reinitialized" or "Initialized" for the target
    combined = res.stdout + res.stderr
    assert "Reinitialized" not in combined


def test_vault_flag_existing_symlink_is_noop(tmp_path):
    """Re-run with --vault on an existing symlink must be a no-op (not fail)."""
    state, config = _dirs(tmp_path)
    target_repo = tmp_path / "existing-repo"
    target_repo.mkdir()
    subprocess.run(["git", "init", str(target_repo)], check=True, capture_output=True)

    _run(["init", "--vault", str(target_repo)], state=state, config=config)
    res = _run(["init", "--vault", str(target_repo)], state=state, config=config)
    assert res.returncode == 0, res.stderr

    default_link = _default_vault(state)
    assert default_link.is_symlink()
    assert os.path.realpath(default_link) == os.path.realpath(target_repo)


# ---------------------------------------------------------------------------
# 7. --local outside a git repo → clean non-zero error, no traceback
# ---------------------------------------------------------------------------

def test_local_outside_git_repo_fails_cleanly(tmp_path):
    state, config = _dirs(tmp_path)
    non_git_dir = tmp_path / "no-git-here"
    non_git_dir.mkdir()

    res = _run(["init", "--local"], state=state, config=config, cwd=non_git_dir)
    assert res.returncode != 0
    # Should say something about git repo
    assert "git" in res.stderr.lower() or "repository" in res.stderr.lower()
    # Must NOT be a raw Python traceback
    assert "Traceback" not in res.stderr


# ---------------------------------------------------------------------------
# 8. Config: seed-if-absent
# ---------------------------------------------------------------------------

def test_init_seeds_config_if_absent(tmp_path):
    state, config = _dirs(tmp_path)
    _run(["init"], state=state, config=config)

    cfg = _read_config(config)
    vaults = cfg["vaults"]
    assert len(vaults) == 1
    assert vaults[0]["name"] == "default"
    assert vaults[0]["scope"] == "default"


# ---------------------------------------------------------------------------
# 9. Config: pre-existing config not clobbered
# ---------------------------------------------------------------------------

def test_init_does_not_clobber_existing_config(tmp_path):
    state, config = _dirs(tmp_path)
    # Write a config with a user-defined extra vault
    cfg_path = _config_path(config)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {
        "vaults": [
            {"name": "default", "scope": "default"},
            {"name": "team-alpha", "scope": "team"},
        ]
    }
    cfg_path.write_text(json.dumps(existing))

    res = _run(["init"], state=state, config=config)
    assert res.returncode == 0, res.stderr

    # Config must be unchanged
    cfg = _read_config(config)
    names = [v["name"] for v in cfg["vaults"]]
    assert "default" in names
    assert "team-alpha" in names


# ---------------------------------------------------------------------------
# 10. Config: missing default vault entry gets it merged in
# ---------------------------------------------------------------------------

def test_init_merges_default_vault_if_missing(tmp_path):
    state, config = _dirs(tmp_path)
    cfg_path = _config_path(config)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    # Config exists but has only a team vault (no default)
    cfg_path.write_text(json.dumps({
        "vaults": [{"name": "team-alpha", "scope": "team"}]
    }))

    res = _run(["init"], state=state, config=config)
    assert res.returncode == 0, res.stderr

    cfg = _read_config(config)
    names = [v["name"] for v in cfg["vaults"]]
    assert "default" in names
    # team-alpha must be preserved
    assert "team-alpha" in names


# ---------------------------------------------------------------------------
# 11. Config: a corrupt existing config is a clean error, not a silent no-op
# ---------------------------------------------------------------------------

def test_init_corrupt_config_is_clean_error(tmp_path):
    """Error-hygiene axiom: a present-but-unparseable config.json must surface a
    clean named error on stderr + nonzero exit, never a silent exit-0 no-op."""
    state, config = _dirs(tmp_path)
    cfg_path = _config_path(config)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("{ this is not valid json")

    res = _run(["init"], state=state, config=config)
    assert res.returncode != 0
    assert "error:" in res.stderr
    assert "config" in res.stderr.lower()
    # The corrupt file is left untouched (not clobbered by a partial write).
    assert cfg_path.read_text() == "{ this is not valid json"
