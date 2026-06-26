"""Slice 2 coverage guard + LORE_VAULT-unset smoke assertions.

Coverage guard (Council Critical #3):
  Files are split into three categories.

  MUST_CALL: files with their own subprocess runners that must explicitly call
    ``write_default_config(`` (with opening paren — an actual call, not just an
    import) using an explicit vault variable, not derived from env.get("LORE_VAULT").

  USES_CONFTEST: files that delegate to conftest's shared ``run_cli`` (which
    already seeds correctly via its explicit ``vault`` param).  The import of
    ``write_default_config`` from conftest satisfies the presence check.

  EXEMPT: files where config seeding is not applicable — either because lore
    init creates the config itself or the tests have no vault ops.

  The accounting test asserts all 19 tracked files are in exactly one category.

Smoke assertions (≥3 structurally distinct helpers):
  Prove that config-based resolution actually reaches the test vault with
  LORE_VAULT popped — across three distinct integration surfaces:

  1. Direct-import: ``vault_config.resolve_active_vault(env=None)`` called
     in-process with XDG_CONFIG_HOME set and LORE_VAULT absent.
  2. Subprocess-spawned (vanilla): ``lore record create`` with LORE_VAULT
     explicitly absent from the subprocess env; config seeded by
     ``write_default_config``.
  3. Layered-vault subprocess: same record-create flow but with a multi-vault
     config (default + a named vault) to prove config resolution also works in
     the layered-vault test pattern.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
REPO_ROOT = TESTS_DIR.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

sys.path.insert(0, str(TESTS_DIR))
from conftest import load_script, write_default_config  # noqa: E402

# ---------------------------------------------------------------------------
# Coverage guard — three-category structure
# ---------------------------------------------------------------------------

# Files that must call write_default_config( with an explicit vault variable
# (not derived from env.get("LORE_VAULT")).  Covers both own-subprocess runners
# and in-process direct-import runners (the latter seed config + fence XDG so
# resolve_active_vault resolves to the test vault — Slice 3).
_MUST_CALL = [
    "test_session_note_resolution.py",
    "test_search_cli.py",
    "test_index_store.py",
    # Slice 3: rewritten from mock.patch LORE_VAULT to config-based resolution.
    "test_layers_model.py",
    "test_layer_discovery.py",
    "test_lore_areas.py",
]

# Files that delegate to conftest.run_cli (which seeds config via explicit vault param).
# Presence of "write_default_config" in source (via conftest import) is sufficient.
_USES_CONFTEST = [
    "test_session_cli.py",
    "test_record_cli_delete_blob.py",
    "test_record_cli_create.py",
    "test_record_cli_update.py",
    "test_vault_routing.py",
]

# Files where seeding is not applicable in Slice 2.  Reason documented inline.
_EXEMPT = {
    "test_lore_cli.py":
        "init tests — lore init creates config itself; no record ops",
    "test_bin_wrapper.py":
        "--help tests — no vault ops",
    "test_lore_init.py":
        "init tests — lore init creates config itself",
    "test_lore_init_hooks.py":
        "init tests — lore init creates config itself",
    "test_lore_guardrail.py":
        "init + guardrail — lore init creates config itself",
    "test_lore_agent_rules.py":
        "init + agent rules — lore init creates config itself",
    "test_trailhead_install_lore.py":
        "install tests — init creates config itself",
    "test_vault_cli.py":
        "vault management — vault add/init creates config itself",
}

_ALL_TRACKED = set(_MUST_CALL) | set(_USES_CONFTEST) | set(_EXEMPT)

_EXPECTED_TRACKED = {
    "test_session_cli.py",
    "test_search_cli.py",
    "test_lore_areas.py",
    "test_session_note_resolution.py",
    "test_vault_cli.py",
    "test_lore_guardrail.py",
    "test_record_cli_delete_blob.py",
    "test_record_cli_create.py",
    "test_index_store.py",
    "test_record_cli_update.py",
    "test_vault_routing.py",
    "test_layers_model.py",
    "test_layer_discovery.py",
    "test_lore_init.py",
    "test_lore_init_hooks.py",
    "test_bin_wrapper.py",
    "test_lore_cli.py",
    "test_lore_agent_rules.py",
    "test_trailhead_install_lore.py",
}


def test_all_tracked_files_accounted_for():
    """Every tracked file is in exactly one category — catches drift when new runners appear."""
    assert _ALL_TRACKED == _EXPECTED_TRACKED, (
        f"Category mismatch.\n"
        f"  In categories but not expected: {_ALL_TRACKED - _EXPECTED_TRACKED}\n"
        f"  Expected but missing from categories: {_EXPECTED_TRACKED - _ALL_TRACKED}"
    )
    total = len(_MUST_CALL) + len(_USES_CONFTEST) + len(_EXEMPT)
    assert total == 19, f"Expected 19 tracked files, got {total}"


@pytest.mark.parametrize("filename", _MUST_CALL)
def test_must_call_file_has_write_default_config_call(filename):
    """MUST_CALL runners must contain write_default_config( (an actual call, not just import).

    The call must use an explicit vault variable — if the source derives the vault from
    env.get("LORE_VAULT"), seeding silently fails when Slice 3 strips that env var.
    """
    src = (TESTS_DIR / filename).read_text(encoding="utf-8")
    assert "write_default_config(" in src, (
        f"{filename}: missing write_default_config() call — the runner must seed "
        f"config from an explicit seed_vault parameter, not from env.get('LORE_VAULT')."
    )
    assert 'env.get("LORE_VAULT")' not in src and "(env or {}).get(\"LORE_VAULT\")" not in src, (
        f"{filename}: runner still derives vault from env.get('LORE_VAULT') — "
        f"this defeats Slice 2's purpose; derive from seed_vault parameter instead."
    )


@pytest.mark.parametrize("filename", _USES_CONFTEST)
def test_conftest_runner_files_import_write_default_config(filename):
    """USES_CONFTEST files must import write_default_config (conftest.run_cli calls it)."""
    src = (TESTS_DIR / filename).read_text(encoding="utf-8")
    assert "write_default_config" in src, (
        f"{filename}: missing write_default_config import — "
        f"this file should delegate to conftest.run_cli which seeds config."
    )


# ---------------------------------------------------------------------------
# LORE_VAULT-unset smoke 1: direct-import — resolve_active_vault via os.environ
# ---------------------------------------------------------------------------


def test_smoke_direct_import_resolve_active_vault_no_lore_vault(tmp_path, monkeypatch):
    """resolve_active_vault(env=None) returns the config-seeded vault with LORE_VAULT unset.

    Structural kind: direct-import (in-process, no subprocess).
    Proves config-based resolution reaches the test vault when LORE_VAULT is absent
    from os.environ — the invariant Slice 3 depends on.
    """
    vault_dir = tmp_path / "test_vault"
    vault_dir.mkdir()
    config_home = tmp_path / "xdg_config"
    state_home = tmp_path / "xdg_state"

    write_default_config(config_home, vault_dir)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.delenv("LORE_VAULT", raising=False)

    vc = load_script("vault_config")
    resolved = vc.resolve_active_vault(env=None)  # env=None → reads os.environ

    assert resolved == vault_dir.resolve(), (
        f"resolve_active_vault(env=None) returned {resolved!r}; "
        f"expected {vault_dir.resolve()!r}. "
        f"Config seeded at {config_home}/lore/config.json."
    )


# ---------------------------------------------------------------------------
# LORE_VAULT-unset smoke 2: subprocess-spawned (vanilla, simple default config)
# ---------------------------------------------------------------------------


def _run_no_lore_vault(args, *, config_home: Path, state_dir: Path, stdin_text=None):
    """Run the lore CLI with XDG_CONFIG_HOME seeded and LORE_VAULT absent."""
    env = dict(os.environ)
    env.pop("LORE_VAULT", None)
    env["XDG_CONFIG_HOME"] = str(config_home)
    env["XDG_STATE_HOME"] = str(state_dir)
    env["LORE_EMAIL"] = "tester@example.com"
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
        input=stdin_text,
    )


def test_smoke_subprocess_vanilla_config_seeded_no_lore_vault(tmp_path):
    """lore record create routes to the config-seeded vault with LORE_VAULT unset.

    Structural kind: subprocess-spawned, vanilla (single default-scope vault).
    The CLI reads XDG_CONFIG_HOME/lore/config.json and routes the record to the
    declared default vault path — proving that the config resolution path is live
    even before Slice 3 strips LORE_VAULT from the harness.
    """
    vault_dir = tmp_path / "seeded_vault"
    vault_dir.mkdir()
    config_home = tmp_path / "cfg"
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    write_default_config(config_home, vault_dir)

    result = _run_no_lore_vault(
        ["record", "create", "--kind", "blob", "--title", "Smoke2", "--keyword", "smoke"],
        config_home=config_home,
        state_dir=state_dir,
        stdin_text="Smoke-2 body: subprocess vanilla config-seeded no LORE_VAULT\n",
    )
    assert result.returncode == 0, (
        f"CLI returned {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    record_id = result.stdout.strip()
    assert record_id.startswith("blob/"), (
        f"Unexpected record_id: {record_id!r}\nstderr: {result.stderr}"
    )
    # The record body must land inside vault_dir (not ~/lore or state floor).
    assert any(vault_dir.rglob("*.md")), (
        f"No .md files found under seeded vault {vault_dir}. "
        f"Record likely routed to wrong vault. stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# LORE_VAULT-unset smoke 3: layered-vault subprocess (multi-vault config)
# ---------------------------------------------------------------------------


def _write_layered_config(config_home: Path, state: Path) -> dict[str, Path]:
    """Seed a multi-vault config (default + named repo vault) under config_home."""
    vaults_root = state / "lore" / "vaults"
    default_path = vaults_root / "default"
    repo_path = vaults_root / "repo"
    default_path.mkdir(parents=True, exist_ok=True)
    repo_path.mkdir(parents=True, exist_ok=True)
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    (lore_cfg / "config.json").write_text(
        json.dumps(
            {
                "vaults": [
                    {"name": "default", "scope": "default", "path": str(default_path)},
                    {
                        "name": "repo",
                        "scope": "repo",
                        "records": ["spec", "plan"],
                        "path": str(repo_path),
                    },
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"default": default_path, "repo": repo_path}


def test_smoke_subprocess_layered_config_no_scope_lands_in_default(tmp_path):
    """lore record create with no scope flags lands in the default vault when LORE_VAULT unset.

    Structural kind: subprocess-spawned, layered-vault config (multi-vault).
    Exercises the same config structure used by test_vault_routing layered tests,
    proving config resolution discriminates the default vault correctly even with
    a non-trivial config file present.
    """
    config_home = tmp_path / "cfg"
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    vaults = _write_layered_config(config_home, state_dir)

    result = _run_no_lore_vault(
        ["record", "create", "--kind", "blob", "--title", "Smoke3", "--keyword", "smoke"],
        config_home=config_home,
        state_dir=state_dir,
        stdin_text="Smoke-3 body: layered config default route no LORE_VAULT\n",
    )
    assert result.returncode == 0, (
        f"CLI returned {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    record_id = result.stdout.strip()
    assert record_id.startswith("blob/"), f"Unexpected record_id: {record_id!r}"

    default_vault = vaults["default"]
    repo_vault = vaults["repo"]
    assert any(default_vault.rglob("*.md")), (
        f"No .md files in default vault {default_vault}; "
        f"record should have landed there (no scope flags → default)."
    )
    assert not any(repo_vault.rglob("*.md")), (
        f"Record landed in repo vault {repo_vault} unexpectedly (no scope flags given)."
    )
