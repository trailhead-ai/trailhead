"""Pins ``_resolve_all_vaults`` and ``_resolve_all_vaults_and_shared`` to
agree on the vault list + error, so a later edit to one cannot silently
diverge ``lore areas``'s vault set from ``sync``/``status``'s. The two
helpers duplicate the same floor-vault / except-tuple logic on purpose (one
also derives the shared-path set from the same read); this test is what
catches the two definitions drifting apart.
"""

from __future__ import annotations

import json
from unittest import mock


def _load_common():
    import importlib
    from lore.cli import common
    return importlib.reload(common)


def _env(config_home, state_home):
    return mock.patch.dict(
        __import__("os").environ,
        {"XDG_CONFIG_HOME": str(config_home), "XDG_STATE_HOME": str(state_home)},
        clear=False,
    )


def test_agrees_with_plain_resolve_all_vaults_on_valid_config(tmp_path):
    config_home = tmp_path / "_xdg_config"
    state_home = tmp_path / "_xdg_state"
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True)
    default_vault = tmp_path / "default_vault"
    (lore_cfg / "config.json").write_text(
        json.dumps(
            {"vaults": [{"name": "default", "scope": "default", "path": str(default_vault)}]}
        ),
        encoding="utf-8",
    )

    common_mod = _load_common()
    with _env(config_home, state_home):
        plain_vaults, plain_error = common_mod._resolve_all_vaults()
        combined_vaults, _shared, combined_error = common_mod._resolve_all_vaults_and_shared()

    assert plain_vaults == combined_vaults
    assert plain_error == combined_error


def test_agrees_with_plain_resolve_all_vaults_on_malformed_config(tmp_path):
    config_home = tmp_path / "_xdg_config"
    state_home = tmp_path / "_xdg_state"
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True)
    (lore_cfg / "config.json").write_text("{not valid json", encoding="utf-8")

    common_mod = _load_common()
    with _env(config_home, state_home):
        plain_vaults, plain_error = common_mod._resolve_all_vaults()
        combined_vaults, _shared, combined_error = common_mod._resolve_all_vaults_and_shared()

    assert plain_vaults == combined_vaults
    assert (plain_error is None) == (combined_error is None)


def test_agrees_with_plain_resolve_all_vaults_when_no_config(tmp_path):
    config_home = tmp_path / "_xdg_config"
    state_home = tmp_path / "_xdg_state"

    common_mod = _load_common()
    with _env(config_home, state_home):
        plain_vaults, plain_error = common_mod._resolve_all_vaults()
        combined_vaults, _shared, combined_error = common_mod._resolve_all_vaults_and_shared()

    assert plain_vaults == combined_vaults
    assert plain_error == combined_error is None
