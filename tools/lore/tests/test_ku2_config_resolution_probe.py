"""KU2 assumption probe — config-only-vault-resolution plan, Slice 2.

Proves (or disproves): a test helper can seed a ``config.json`` with an
explicit absolute default-vault ``path``, and the lore CLI subprocess resolves
records to that vault when ``LORE_VAULT`` is **not** set in the environment.

Specifically verifies:
1. ``XDG_CONFIG_HOME`` seeded with a ``config.json`` (explicit absolute path)
   is found by the CLI subprocess via ``_resolve_config_path()`` / ``_load_vault_config()``.
2. ``lore record create`` with no routing flags routes the record to the
   config-declared default vault when ``LORE_VAULT`` is absent.
3. ``vault_config.resolve_active_vault(env=None)`` — called with no env override
   so it reads ``os.environ`` — returns the config-seeded path (not the floor,
   not ``~/lore``).

This test is **ephemeral** (assumption probe).  The executor will remove it
after the slice is built and proper behavioral tests are in place.

CLEAN UP: delete this file entirely (tests/test_ku2_config_resolution_probe.py).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CLI_PATH = REPO_ROOT / "plugins" / "lore" / "cli" / "lore"
SCRIPTS_DIR = REPO_ROOT / "plugins" / "lore" / "scripts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_config(config_home: Path, vault_path: Path) -> Path:
    """Write config.json with a single default-scope vault at an explicit absolute path."""
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    cfg_path = lore_cfg / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "vaults": [
                    {
                        "name": "default",
                        "scope": "default",
                        "path": str(vault_path),
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return cfg_path


def _run_lore_no_vault_var(args, *, config_home: Path, state_dir: Path, stdin_text=None):
    """Run the lore CLI with XDG_CONFIG_HOME seeded and LORE_VAULT explicitly absent."""
    env = dict(os.environ)
    env.pop("LORE_VAULT", None)          # the critical line: no LORE_VAULT
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


def _load_vault_config_mod():
    """Load vault_config.py freshly via importlib (mirrors conftest.load_script)."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    name = "vault_config_ku2_probe"
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / "vault_config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# KU2 test 1 — end-to-end: record lands in the config-seeded vault
# ---------------------------------------------------------------------------


def test_ku2_record_create_routes_to_config_seeded_vault(tmp_path):
    """KU2 core: record create with no routing flags routes to the config default vault.

    Seeds XDG_CONFIG_HOME/lore/config.json with an explicit absolute path to a
    tmp vault dir.  Pops LORE_VAULT from the subprocess environment.  Asserts the
    created record body lands under the seeded vault, NOT ~/lore or the state floor.
    """
    # The vault the config declares — an arbitrary tmp dir (not under state/vaults/).
    vault_dir = tmp_path / "my_seeded_vault"
    vault_dir.mkdir()

    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    _seed_config(config_home, vault_dir)

    result = _run_lore_no_vault_var(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            "KU2 assumption probe",
            "--keyword",
            "ku2",
        ],
        config_home=config_home,
        state_dir=state_dir,
        stdin_text="KU2 probe body — verifying config-seeded vault resolution\n",
    )

    assert result.returncode == 0, (
        f"CLI exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    record_id = result.stdout.strip()
    assert record_id.startswith("blob/"), (
        f"Unexpected record_id on stdout: {record_id!r}\nstderr: {result.stderr}"
    )

    # The record body must land inside vault_dir.
    blob_dir = vault_dir / "blob"
    assert blob_dir.exists(), (
        f"Expected blob/ dir at {blob_dir} to exist, but it does not.  "
        f"The record likely landed in the WRONG vault (~/lore or the floor).  "
        f"record_id={record_id!r}  stderr={result.stderr!r}"
    )
    record_files = list(blob_dir.rglob("*.md"))
    assert record_files, (
        f"blob/ dir exists but contains no .md files — unexpected.  "
        f"record_id={record_id!r}  stderr={result.stderr!r}"
    )

    # Belt-and-suspenders: confirm the file path starts with vault_dir
    for f in record_files:
        assert str(f).startswith(str(vault_dir)), (
            f"Record body {f} is NOT under the seeded vault {vault_dir}"
        )


# ---------------------------------------------------------------------------
# KU2 test 2 — resolve_active_vault honors XDG_CONFIG_HOME via os.environ
# ---------------------------------------------------------------------------


def test_ku2_resolve_active_vault_reads_xdg_config_home(tmp_path, monkeypatch):
    """KU2 unit: resolve_active_vault(env=None) reads XDG_CONFIG_HOME from os.environ.

    Uses monkeypatch.setenv to set XDG_CONFIG_HOME in-process (the same variable
    the CLI subprocess would have) and calls resolve_active_vault(env=None) to
    confirm it returns the config-seeded path, not the floor.

    This directly tests the in-process resolver path that Slice 3 will wire into
    the CLI subprocess.
    """
    vault_dir = tmp_path / "ku2_unit_vault"
    vault_dir.mkdir()

    config_home = tmp_path / "xdg_config"
    state_dir = tmp_path / "xdg_state"

    _seed_config(config_home, vault_dir)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_dir))
    monkeypatch.delenv("LORE_VAULT", raising=False)

    vc = _load_vault_config_mod()
    resolved = vc.resolve_active_vault(env=None)  # env=None → uses os.environ

    assert resolved == vault_dir.resolve(), (
        f"resolve_active_vault(env=None) returned {resolved!r}; "
        f"expected {vault_dir.resolve()!r}.  "
        f"Config-home seeded at {config_home}, XDG_CONFIG_HOME={os.environ.get('XDG_CONFIG_HOME')!r}"
    )
