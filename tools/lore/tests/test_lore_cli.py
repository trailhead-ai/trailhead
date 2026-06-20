"""Tests for the `lore` CLI (init / set-status / stats).

The CLI is exercised as a subprocess so we test the real executable + its
sibling-module import path, exit codes, and stdout/stderr.

The ``init`` tests here cover the non-interactive idempotent installer semantics
introduced in Slice 1, S5. The old interactive scaffolding (positional path,
--yes, --allow-outside-home, --force, taxonomy dirs, harvest-pending.md) has
been removed.
"""

import os
import subprocess
import sys

from conftest import CLI_PATH, load_script


def run_cli(args, env=None, input_text=None, cwd=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env, input=input_text,
        cwd=str(cwd) if cwd else None,
    )


def _state_and_config(tmp_path):
    """Return isolated XDG state + config dirs."""
    state = tmp_path / "state"
    config = tmp_path / "config"
    state.mkdir(parents=True, exist_ok=True)
    config.mkdir(parents=True, exist_ok=True)
    return state, config


def _run_init(tmp_path, extra_args=None, cwd=None):
    state, config = _state_and_config(tmp_path)
    args = ["init"] + (extra_args or [])
    env = {
        "XDG_STATE_HOME": str(state),
        "XDG_CONFIG_HOME": str(config),
    }
    return run_cli(args, env=env, cwd=cwd), state, config


# ---- lore init: non-interactive idempotent installer (Slice 1, S5) ----------

def test_init_exits_zero(tmp_path):
    """lore init with no args exits 0."""
    res, _, _ = _run_init(tmp_path)
    assert res.returncode == 0, res.stderr


def test_init_creates_default_vault_as_git_repo(tmp_path):
    """init bootstraps $XDG_STATE_HOME/lore/vaults/default as a git repo."""
    res, state, _ = _run_init(tmp_path)
    assert res.returncode == 0, res.stderr
    default_vault = state / "lore" / "vaults" / "default"
    assert default_vault.is_dir()
    assert (default_vault / ".git").is_dir()


def test_init_idempotent_no_reinit(tmp_path):
    """Re-running init does not re-git-init the vault (no Reinitialized message)."""
    _run_init(tmp_path)
    res, _, _ = _run_init(tmp_path)
    assert res.returncode == 0, res.stderr
    assert "Reinitialized" not in (res.stdout + res.stderr)


def test_init_no_harvest_pending(tmp_path):
    """init must NOT create harvest-pending.md (removed in Slice 1, S5)."""
    res, state, _ = _run_init(tmp_path)
    assert res.returncode == 0, res.stderr
    default_vault = state / "lore" / "vaults" / "default"
    assert not (default_vault / "harvest-pending.md").exists()


# ---- lore patch is unregistered ---------------------------------------------

def test_patch_subcommand_is_unregistered(tmp_path):
    """The orphaned `lore patch` subcommand was removed: invoking it is an
    argparse 'invalid choice' error (exit 2), never a successful patch. The
    internal `frontmatter.patch_section` helper (used by `lore handoff`) is
    unaffected — only the user-facing subcommand is gone."""
    p = tmp_path / "s.md"
    p.write_text("---\ntype: session\nstatus: active\n---\n\n## What we did\nx\n")
    r = run_cli(["patch", str(p), "What we did", "--text", "- more work"])
    assert r.returncode == 2
    assert "invalid choice: 'patch'" in r.stderr


# ---- lore set-status --------------------------------------------------------

def test_set_status_rejects_noncanonical(tmp_path):
    p = tmp_path / "d.md"
    original = "---\ntype: deferred\nstatus: open\n---\n\nbody\n"
    p.write_text(original)
    r = run_cli(["set-status", str(p), "bogus"])
    assert r.returncode != 0
    assert p.read_text() == original  # no write


def test_set_status_accepts_canonical(tmp_path):
    p = tmp_path / "d.md"
    p.write_text("---\ntype: deferred\nstatus: open\n---\n\nbody\n")
    r = run_cli(["set-status", str(p), "resolved"])
    assert r.returncode == 0, r.stderr
    fm = load_script("frontmatter")
    assert fm.parse_frontmatter(p)["status"] == "resolved"


# ---- lore stats -------------------------------------------------------------

def test_stats_counts_resolved_vault(tmp_path):
    # Build a minimal vault directory by hand (init no longer scaffolds taxonomy
    # dirs in a user-specified location — it bootstraps vaults/default).
    target = tmp_path / "vault"
    (target / "deferred").mkdir(parents=True)
    (target / "deferred" / "x.md").write_text(
        "---\ntype: deferred\nstatus: open\n---\n\n# X\n"
    )
    r = run_cli(["stats"], env={"LORE_VAULT": str(target)})
    assert r.returncode == 0, r.stderr
    assert "deferred" in r.stdout.lower()
    assert "1" in r.stdout
