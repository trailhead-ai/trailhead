"""Assumption probe for U1 — degrade posture on a broken config.

EPHEMERAL — proves which of ``_resolve_all_vaults_and_shared()`` /
``_resolve_all_vaults_strict()`` the new ``lore pipeline`` command should
call. Not part of the permanent suite; delete this file once Slice 1's real
tests land (see the prover's report for the exact cleanup instruction).

Question: does the pipeline command's exit-code contract ("nonzero only
when no configured vault could be read", shared-name set must come from the
SAME config read as the vault list) fit cleanly onto either resolver as-is?
"""

from __future__ import annotations

import importlib
import json
import sys

import pytest


def _load_common():
    from lore.cli import common
    return importlib.reload(common)


def _write_config(config_dir, vaults):
    lore_cfg = config_dir / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    (lore_cfg / "config.json").write_text(json.dumps({"vaults": vaults}), encoding="utf-8")
    return lore_cfg / "config.json"


# ---------------------------------------------------------------------------
# _resolve_all_vaults_and_shared() — the three cases
# ---------------------------------------------------------------------------


def test_and_shared__missing_config_degrades_to_floor_with_no_error(tmp_path, monkeypatch):
    """No config.json at all: floor vault, empty shared set, error is None.

    This is vanilla usage (Axiom 3), not a broken-config case — the function
    does NOT flag this as an error.
    """
    common_mod = _load_common()
    vaults, shared, error = common_mod._resolve_all_vaults_and_shared()

    assert error is None
    assert shared == set()
    assert len(vaults) == 1
    assert vaults[0][0] == "default"


def test_and_shared__unparseable_config_degrades_to_floor_with_error(tmp_path, monkeypatch):
    """Present-but-broken config.json: SAME synthetic floor vault, but now
    a non-None error string names the problem.
    """
    config_dir = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    (config_dir / "lore").mkdir(parents=True)
    (config_dir / "lore" / "config.json").write_text("{not valid json", encoding="utf-8")

    common_mod = _load_common()
    vaults, shared, error = common_mod._resolve_all_vaults_and_shared()

    assert error is not None
    assert "config.json" in error or "cannot read" in error
    assert shared == set()
    assert len(vaults) == 1
    assert vaults[0][0] == "default"
    # Critical: the degraded floor vault is INDISTINGUISHABLE from the
    # vanilla no-config case by shape alone — only `error` tells them apart.


def test_and_shared__valid_config_derives_vault_list_and_shared_names_from_one_read(
    tmp_path, monkeypatch
):
    """Valid config.json with a shared vault: real vault list + shared-name
    set, both derived from a single ``load_config`` call.
    """
    config_dir = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    default_vault = tmp_path / "vaults" / "default"
    shared_vault = tmp_path / "vaults" / "team"
    _write_config(
        config_dir,
        [
            {"name": "default", "scope": "default", "path": str(default_vault)},
            {"name": "team", "scope": "team", "path": str(shared_vault), "shared": True},
        ],
    )

    common_mod = _load_common()

    call_count = {"n": 0}
    real_load_config = common_mod.vault_config_mod.load_config

    def counting_load_config(*a, **kw):
        call_count["n"] += 1
        return real_load_config(*a, **kw)

    monkeypatch.setattr(common_mod.vault_config_mod, "load_config", counting_load_config)

    vaults, shared, error = common_mod._resolve_all_vaults_and_shared()

    assert error is None
    assert call_count["n"] == 1, "vault list + shared set must come from ONE config read"
    assert {name for name, _ in vaults} == {"default", "team"}
    assert shared == {"team"}


# ---------------------------------------------------------------------------
# _resolve_all_vaults_strict() — same three cases, plus: does it give shared
# names without a second config read?
# ---------------------------------------------------------------------------


def test_strict__missing_config_does_NOT_refuse(tmp_path, monkeypatch, capsys):
    """Strict calls _resolve_all_vaults(), which is error=None on a missing
    config.json (vanilla usage) — so strict does NOT refuse here either.

    This disproves the naive assumption that 'strict' means 'refuses on
    missing-or-unparseable config' — it only refuses when `_resolve_all_vaults`
    reports a non-None error, which vanilla (no-file) usage never does.
    """
    common_mod = _load_common()
    result = common_mod._resolve_all_vaults_strict("pipeline")

    assert result is not None
    assert len(result) == 1
    assert result[0][0] == "default"
    captured = capsys.readouterr()
    assert captured.err == ""  # no refusal diagnostic printed


def test_strict__unparseable_config_DOES_refuse(tmp_path, monkeypatch, capsys):
    config_dir = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    (config_dir / "lore").mkdir(parents=True)
    (config_dir / "lore" / "config.json").write_text("{not valid json", encoding="utf-8")

    common_mod = _load_common()
    result = common_mod._resolve_all_vaults_strict("pipeline")

    assert result is None
    captured = capsys.readouterr()
    assert "Aborting" in captured.err
    assert "pipeline" in captured.err
    # Note: this diagnostic shape ("error: ...\n  Aborting — refusing to
    # pipeline...") is NOT the `lore: <message>` single-line convention the
    # other cmd_* handlers use (dispatch.py / record.py:458) — a caller
    # using this function verbatim would emit a differently-shaped stderr
    # line than every other lore subcommand's failure posture.


def test_strict__valid_config_gives_no_shared_names_and_a_second_read_is_needed(
    tmp_path, monkeypatch
):
    """Strict's return type is `list[tuple[str, Path]] | None` — no shared
    set. Getting shared names requires `_shared_vault_paths()`, which calls
    `_load_vault_config()` — parsing config.json AGAIN. This is exactly the
    read-divergence risk `_resolve_all_vaults_and_shared()`'s docstring names
    as the reason it exists.

    SURPRISE: `_resolve_all_vaults_strict()` -> `_resolve_all_vaults()`
    already costs TWO `load_config` calls on the valid-config path by
    itself, before shared names ever enter the picture —
    `_resolve_all_vaults()` unconditionally computes
    `floor = [("default", Path(vault_config_mod.resolve_active_vault()))]`
    (which itself calls `load_config`) BEFORE checking whether
    `config_path.exists()`, then calls `load_config` again explicitly in its
    own try block. That eagerly-computed floor is discarded on the
    valid-config path but the read already happened. Adding
    `_shared_vault_paths()` for shared names makes THREE reads total.
    `_resolve_all_vaults_and_shared()` avoids this: it only resolves the
    floor lazily, on the two branches that actually need it, so the
    valid-config path costs exactly one `load_config` call (proven above).
    """
    config_dir = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    default_vault = tmp_path / "vaults" / "default"
    shared_vault = tmp_path / "vaults" / "team"
    _write_config(
        config_dir,
        [
            {"name": "default", "scope": "default", "path": str(default_vault)},
            {"name": "team", "scope": "team", "path": str(shared_vault), "shared": True},
        ],
    )

    common_mod = _load_common()

    call_count = {"n": 0}
    real_load_config = common_mod.vault_config_mod.load_config

    def counting_load_config(*a, **kw):
        call_count["n"] += 1
        return real_load_config(*a, **kw)

    monkeypatch.setattr(common_mod.vault_config_mod, "load_config", counting_load_config)

    result = common_mod._resolve_all_vaults_strict("pipeline")
    assert call_count["n"] == 2, (
        "_resolve_all_vaults_strict() -> _resolve_all_vaults() already costs "
        "TWO load_config reads on the valid-config path alone (an eagerly-"
        "computed, discarded floor via resolve_active_vault(), plus the "
        "explicit read) — before shared names are even requested"
    )

    # Nothing in `result` carries shared-ness — getting it costs a 3rd read:
    shared_paths = common_mod._shared_vault_paths()
    assert call_count["n"] == 3, (
        "using _resolve_all_vaults_strict() plus _shared_vault_paths() to "
        "get shared names costs a THIRD config.json read, violating the "
        "one-read property _resolve_all_vaults_and_shared() exists for"
    )
    assert shared_paths == {str(shared_vault.resolve())}


# ---------------------------------------------------------------------------
# The shape U1's point 3 proposes: call the degrading variant, but treat the
# error-string case as the nonzero path (don't render the synthetic floor
# vault as if it were the configured set).
# ---------------------------------------------------------------------------


def test_proposed_shape__error_is_not_none_is_the_reliable_nonzero_discriminator(
    tmp_path, monkeypatch
):
    """Proves the discriminator a pipeline cmd_* handler would branch on:
    `error is not None` from `_resolve_all_vaults_and_shared()` — NOT
    "config.json is missing" — is what separates "no configured vault could
    be read" (unparseable config) from "vanilla usage, one real vault"
    (missing config).
    """
    common_mod = _load_common()

    # Case 1: missing config.json -> proceed (error is None), vanilla floor.
    _, _, error_missing = common_mod._resolve_all_vaults_and_shared()
    assert error_missing is None

    # Case 2: unparseable config.json -> refuse (error is not None).
    config_dir = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    (config_dir / "lore").mkdir(parents=True)
    (config_dir / "lore" / "config.json").write_text("{not valid json", encoding="utf-8")
    common_mod2 = _load_common()
    _, _, error_broken = common_mod2._resolve_all_vaults_and_shared()
    assert error_broken is not None

    # A `cmd_pipeline` built on `_resolve_all_vaults_and_shared()` therefore
    # branches on `error is not None`, exactly the same discriminator
    # `_resolve_all_vaults_strict()` uses internally -- but WITHOUT paying
    # for a second config read to also get the shared-name set.
