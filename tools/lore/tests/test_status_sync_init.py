"""lore init git-init wiring and lore sync tests.

Covers:

lore init git-init wiring:
  - fresh dir produces a git repo (.git/ exists)
  - init installs NO pre-commit hook (the old vault-integrity hook subsystem was
    retired; record validation now lives in the `lore record` CLI via
    record_model.py)
  - re-running init does not re-initialize an existing repo

lore sync:
  - stages + commits a dirty vault; clean tree → no commit created
  - commit.gpgsign=false: commit succeeds (no signing error)
  - toplevel mismatch (vault is a subdir of a larger repo) → aborts without committing
  - no origin remote → commits but prints a notice about skipping push
  - push failure (offline/auth) → exit 0 with a soft-failure notice; commit is durable
  - custom --message is honored
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"


def run_cli(args, env=None, input_text=None, *, seed_vault=None):
    from conftest import write_default_config
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    if seed_vault is not None:
        _cfg = Path(seed_vault).parent / "_xdg_config"
        full_env["XDG_CONFIG_HOME"] = str(_cfg)
        write_default_config(_cfg, Path(seed_vault))
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=full_env,
        input=input_text,
    )


def _git_init(path: Path, gpg_sign: bool = False) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@e.st"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"], check=True, capture_output=True
    )
    if not gpg_sign:
        subprocess.run(
            ["git", "-C", str(path), "config", "commit.gpgsign", "false"],
            check=True,
            capture_output=True,
        )


def _git_commit_all(vault: Path, msg: str = "init") -> subprocess.CompletedProcess:
    subprocess.run(["git", "-C", str(vault), "add", "-A"], capture_output=True)
    return subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", msg],
        capture_output=True,
        text=True,
    )


# ── lore init git-init wiring (non-interactive installer) ──────
#
# The old tests verified lore init <path> --yes scaffolded a vault at an
# arbitrary user-specified location. That was replaced with a
# non-interactive installer: init now bootstraps $XDG_STATE_HOME/lore/vaults/default.
# The tests below verify the new semantics.


def test_init_creates_git_repo(tmp_path):
    """init bootstraps vaults/default as a git repo under XDG_STATE_HOME."""
    state = tmp_path / "state"
    config = tmp_path / "config"
    state.mkdir(parents=True, exist_ok=True)
    config.mkdir(parents=True, exist_ok=True)
    env = {
        "XDG_STATE_HOME": str(state),
        "XDG_CONFIG_HOME": str(config),
    }
    r = run_cli(["init"], env=env)
    assert r.returncode == 0, r.stderr
    default_vault = state / "lore" / "vaults" / "default"
    assert (default_vault / ".git").is_dir()


def test_init_installs_pre_commit_hook(tmp_path):
    """init does NOT install any pre-commit hook (removed)."""
    state = tmp_path / "state"
    config = tmp_path / "config"
    state.mkdir(parents=True, exist_ok=True)
    config.mkdir(parents=True, exist_ok=True)
    env = {
        "XDG_STATE_HOME": str(state),
        "XDG_CONFIG_HOME": str(config),
    }
    r = run_cli(["init"], env=env)
    assert r.returncode == 0, r.stderr
    default_vault = state / "lore" / "vaults" / "default"
    hook = default_vault / ".git" / "hooks" / "pre-commit"
    # The old installer wrote a pre-commit hook; the new init must NOT.
    assert not hook.exists()


def test_init_skips_git_init_if_already_a_repo(tmp_path):
    """Re-running init on an existing vault does not re-initialize the git repo."""
    state = tmp_path / "state"
    config = tmp_path / "config"
    state.mkdir(parents=True, exist_ok=True)
    config.mkdir(parents=True, exist_ok=True)
    env = {
        "XDG_STATE_HOME": str(state),
        "XDG_CONFIG_HOME": str(config),
    }
    run_cli(["init"], env=env)
    r = run_cli(["init"], env=env)
    assert r.returncode == 0, r.stderr
    assert "Reinitialized" not in (r.stdout + r.stderr)


# ── lore sync ─────────────────────────────────────────────────────────────────


def _make_sync_vault(tmp_path: Path) -> Path:
    """Create a git-initialized vault with an initial commit."""
    vault = tmp_path / "vault"
    _git_init(vault)
    (vault / "sessions").mkdir()
    (vault / "README.md").write_text("vault\n")
    _git_commit_all(vault, "init")
    return vault


def test_sync_commits_dirty_vault(tmp_path):
    vault = _make_sync_vault(tmp_path)
    (vault / "sessions" / "note.md").write_text(
        "---\ntype: session\nstatus: active\n---\n\n# Session\n"
    )
    r = run_cli(["sync"], seed_vault=vault)
    assert r.returncode == 0, r.stderr
    log = subprocess.run(
        ["git", "-C", str(vault), "log", "--oneline"],
        capture_output=True,
        text=True,
    )
    assert len(log.stdout.strip().splitlines()) == 2  # init + sync commit


def test_sync_noop_on_clean_tree(tmp_path):
    vault = _make_sync_vault(tmp_path)
    r = run_cli(["sync"], seed_vault=vault)
    assert r.returncode == 0, r.stderr
    log = subprocess.run(
        ["git", "-C", str(vault), "log", "--oneline"],
        capture_output=True,
        text=True,
    )
    assert len(log.stdout.strip().splitlines()) == 1  # only init, no empty commit


def test_sync_respects_gpgsign_false(tmp_path):
    """With commit.gpgsign=false in the repo config, sync commits succeed unsigned."""
    vault = _make_sync_vault(tmp_path)
    (vault / "sessions" / "note.md").write_text(
        "---\ntype: session\nstatus: active\n---\n\n# Session\n"
    )
    r = run_cli(["sync"], seed_vault=vault)
    assert r.returncode == 0, r.stderr + r.stdout
    log = subprocess.run(
        ["git", "-C", str(vault), "log", "--oneline"],
        capture_output=True,
        text=True,
    )
    assert len(log.stdout.strip().splitlines()) == 2


def test_sync_aborts_on_toplevel_mismatch(tmp_path):
    """Vault is a subdir of a larger repo — sync must not commit the parent."""
    parent = tmp_path / "parent"
    _git_init(parent)
    (parent / "README.md").write_text("parent\n")
    _git_commit_all(parent, "parent init")

    # vault subdir — NOT its own git repo
    vault = parent / "vault-subdir"
    vault.mkdir()
    (vault / "sessions").mkdir()

    r = run_cli(["sync"], seed_vault=vault)
    assert r.returncode != 0
    # Parent repo should be unmodified
    log = subprocess.run(
        ["git", "-C", str(parent), "log", "--oneline"],
        capture_output=True,
        text=True,
    )
    assert len(log.stdout.strip().splitlines()) == 1


def test_sync_skips_push_without_origin(tmp_path):
    """No origin remote → commit is made, push is skipped with a notice."""
    vault = _make_sync_vault(tmp_path)
    (vault / "README.md").write_text("vault updated\n")
    r = run_cli(["sync"], seed_vault=vault)
    assert r.returncode == 0, r.stderr
    combined = r.stdout + r.stderr
    assert (
        "origin" in combined.lower() or "push" in combined.lower() or "remote" in combined.lower()
    )


def test_sync_accepts_custom_message(tmp_path):
    vault = _make_sync_vault(tmp_path)
    (vault / "README.md").write_text("vault updated\n")
    r = run_cli(["sync", "--message", "my custom commit"], seed_vault=vault)
    assert r.returncode == 0, r.stderr
    log = subprocess.run(
        ["git", "-C", str(vault), "log", "--oneline"],
        capture_output=True,
        text=True,
    )
    assert "my custom commit" in log.stdout


# ── lore sync exit code after push failure ────────────────────────────────


def _make_sync_vault_with_failing_remote(tmp_path: Path) -> Path:
    """Create a vault with an origin that will fail to push."""
    vault = tmp_path / "vault"
    _git_init(vault)
    (vault / "sessions").mkdir()
    (vault / "README.md").write_text("vault\n")
    _git_commit_all(vault, "init")

    # Add a remote that doesn't exist → push will fail
    subprocess.run(
        [
            "git",
            "-C",
            str(vault),
            "remote",
            "add",
            "origin",
            "git@github.com:nonexistent-test-org-xyz/nonexistent-repo.git",
        ],
        check=True,
        capture_output=True,
    )
    return vault


def test_sync_exits_zero_when_push_fails_but_commit_succeeds(tmp_path):
    """When commit succeeds but push fails (offline/auth), lore sync must exit 0
    and print a prominent notice — commit is durable, push failure is soft."""
    vault = _make_sync_vault_with_failing_remote(tmp_path)
    (vault / "README.md").write_text("vault updated\n")

    r = run_cli(["sync"], seed_vault=vault)
    assert r.returncode == 0, (
        f"sync must exit 0 when commit succeeded but push failed; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    combined = r.stdout + r.stderr
    # Must have printed a notice about push failure / re-run
    assert (
        "push failed" in combined.lower()
        or "re-run" in combined.lower()
        or "online" in combined.lower()
        or "lore sync" in combined
    ), f"sync must print a soft-failure notice; got: {combined!r}"

    # The commit must have been made
    log = subprocess.run(
        ["git", "-C", str(vault), "log", "--oneline"],
        capture_output=True,
        text=True,
    )
    assert len(log.stdout.strip().splitlines()) == 2, (
        "commit must have been made before push was attempted"
    )


# ── lore init completes successfully ───────────────────────
#
# The old test verified that a planned-tree preview text matched directories created
# by the interactive scaffolding. The interactive scaffolding was removed
# entirely. The new invariant is simply: init exits 0 and the canonical vault exists.


def test_init_planned_tree_shows_both_template_dirs(tmp_path):
    """init exits 0 and bootstraps the default vault."""
    state = tmp_path / "state"
    config = tmp_path / "config"
    state.mkdir(parents=True, exist_ok=True)
    config.mkdir(parents=True, exist_ok=True)
    env = {
        "XDG_STATE_HOME": str(state),
        "XDG_CONFIG_HOME": str(config),
    }
    r = run_cli(["init"], env=env)
    assert r.returncode == 0, r.stderr
    assert (state / "lore" / "vaults" / "default").is_dir()
