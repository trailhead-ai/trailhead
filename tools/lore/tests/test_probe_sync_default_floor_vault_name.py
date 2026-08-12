"""ASSUMPTION PROBE (ephemeral — delete before merge).

Resolves the Known Unknown blocking slice `outpost-publish-site-skill` of
plan `task/vault-hosted-static-sites-served-by-outpost`:

    What `--vault` name does `lore sync` accept when `lore vault resolve`
    lands on the default floor (`vault: null`)? Is `default` a normalizable
    name lore sync will recognize?

Two scenarios cover the default-floor case, because the default-scope
vault's *configured name* is not forced to be the literal string
"default" (see ``vault/config.py::validate_config`` — only ``scope`` is
constrained, not ``name``):

  A. No ``config.json`` at all (vanilla usage) — ``_resolve_all_vaults``
     synthesizes the literal name ``"default"`` for the floor vault
     (``cli/common.py:185``), so ``--vault default`` should match.
  B. A ``config.json`` exists with a default-scope vault configured under
     a DIFFERENT name (e.g. ``"trailhead"``) — the real shape a machine
     with lore already initialized is in. ``vault resolve --json`` still
     reports ``vault: null`` for this case (any ``scope == "default"``
     resolution nulls the name — ``cli/vault.py:459-462``), but
     ``_resolve_all_vaults`` returns the vault under its REAL configured
     name (``cli/common.py:192``), not the string "default".

If scenario B rejects ``--vault default``, the publish script cannot use a
hardcoded/normalized "default" placeholder in the general case — it needs a
different targeting strategy.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

CONFTEST_DIR = Path(__file__).parent
sys.path.insert(0, str(CONFTEST_DIR))
from conftest import CLI_PATH  # noqa: E402


def _dirs(tmp_path):
    state = tmp_path / "state"
    config = tmp_path / "config"
    state.mkdir()
    config.mkdir()
    return state, config


def _run(args, *, state, config, cwd=None):
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(state)
    env["XDG_CONFIG_HOME"] = str(config)
    env["HOME"] = str(state / "home")
    env["LORE_EMAIL"] = "tester@example.com"
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd is not None else None,
    )


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)


def _make_git_vault(path: Path) -> Path:
    """A minimal git vault — enough for sync to reach its --vault name check."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    for key, val in (("user.email", "t@e.st"), ("user.name", "Test"), ("commit.gpgsign", "false")):
        _git(path, "config", key, val)
    (path / "README.md").write_text("vault\n")
    (path / ".gitignore").write_text("*.lock\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "init")
    return path


# ---------------------------------------------------------------------------
# Scenario A: no config.json — vanilla single-floor-vault machine
# ---------------------------------------------------------------------------


def test_vanilla_no_config_resolve_reports_null_and_sync_accepts_default(tmp_path):
    state, config = _dirs(tmp_path)
    # No config.json written at all — the vanilla-usage floor case.

    resolve = _run(
        ["vault", "resolve", "--kind", "blob", "--json"], state=state, config=config
    )
    assert resolve.returncode == 0, resolve.stderr
    payload = json.loads(resolve.stdout)
    assert payload["vault"] is None
    assert payload["scope"] == "default"
    assert payload["path"] == str(state / "lore" / "vaults" / "default")

    # Make the synthesized floor vault a real git repo so sync can reach its
    # name-targeting logic instead of failing earlier on "vault not found".
    _make_git_vault(state / "lore" / "vaults" / "default")

    sync = _run(["sync", "--vault", "default"], state=state, config=config)
    assert sync.returncode == 0, f"stdout={sync.stdout!r} stderr={sync.stderr!r}"
    assert "unknown vault" not in sync.stderr


# ---------------------------------------------------------------------------
# Scenario B: config.json exists, default-scope vault has a NON-"default" name
# ---------------------------------------------------------------------------


def test_configured_default_vault_with_custom_name_rejects_literal_default(tmp_path):
    state, config = _dirs(tmp_path)
    vault_path = state / "lore" / "vaults" / "trailhead"
    _make_git_vault(vault_path)

    lore_cfg = config / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    (lore_cfg / "config.json").write_text(
        json.dumps(
            {
                "vaults": [
                    {
                        "name": "trailhead",
                        "scope": "default",
                        "path": str(vault_path),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    resolve = _run(
        ["vault", "resolve", "--kind", "blob", "--json"], state=state, config=config
    )
    assert resolve.returncode == 0, resolve.stderr
    payload = json.loads(resolve.stdout)
    # Confirms the unknown's premise: resolution lands on the default floor
    # and the JSON's `vault` field is null EVEN THOUGH the vault has a real,
    # different configured name ("trailhead").
    assert payload["vault"] is None
    assert payload["scope"] == "default"
    assert payload["path"] == str(vault_path)

    # The literal string "default" is NOT the vault's real name here — sync
    # must refuse it exactly like any other unknown vault name.
    sync_default = _run(["sync", "--vault", "default"], state=state, config=config)
    assert sync_default.returncode == 1
    assert "unknown vault: 'default'" in sync_default.stderr
    assert "trailhead" in sync_default.stderr  # printed in "configured vaults: ..."

    # The vault's REAL configured name works.
    sync_real_name = _run(["sync", "--vault", "trailhead"], state=state, config=config)
    assert sync_real_name.returncode == 0, (
        f"stdout={sync_real_name.stdout!r} stderr={sync_real_name.stderr!r}"
    )

    # The no-flag fallback (sync every configured vault) also reaches and
    # syncs the default-floor vault, without needing to know its name at all.
    sync_all = _run(["sync"], state=state, config=config)
    assert sync_all.returncode == 0, f"stdout={sync_all.stdout!r} stderr={sync_all.stderr!r}"
