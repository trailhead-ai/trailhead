"""Tests for the `lore` CLI (init / stats).

The CLI is exercised as a subprocess so we test the real executable + its
sibling-module import path, exit codes, and stdout/stderr.

The ``init`` tests here cover the non-interactive idempotent installer semantics.
The old interactive scaffolding (positional path, --yes, --allow-outside-home,
--force, taxonomy dirs, harvest-pending.md) has been removed.
"""

import os
import subprocess
import sys
from pathlib import Path

from conftest import CLI_PATH, write_default_config


def run_cli(args, env=None, input_text=None, cwd=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    _vault = (env or {}).get("LORE_VAULT")
    if _vault:
        _cfg = Path(_vault).parent / "_xdg_config"
        full_env.setdefault("XDG_CONFIG_HOME", str(_cfg))
        write_default_config(_cfg, Path(_vault))
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=full_env,
        input=input_text,
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


# ---- lore init: non-interactive idempotent installer ----------


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
    """init must NOT create harvest-pending.md (removed)."""
    res, state, _ = _run_init(tmp_path)
    assert res.returncode == 0, res.stderr
    default_vault = state / "lore" / "vaults" / "default"
    assert not (default_vault / "harvest-pending.md").exists()


# ---- lore set-status (removed — redirects via dispatch hint) ----------------


def test_set_status_removed_and_hints_replacement(tmp_path):
    """`lore set-status` was removed. It must exit non-zero and stderr must carry
    the `_DISPATCH_HINTS` redirect pointing agents to `lore record update --status`."""
    p = tmp_path / "d.md"
    p.write_text("---\ntype: deferred\nstatus: open\n---\n\nbody\n")
    r = run_cli(["set-status", str(p), "ready"])
    assert r.returncode != 0
    assert "unknown command 'set-status'" in r.stderr
    assert "did you mean 'lore record update --status'?" in r.stderr
