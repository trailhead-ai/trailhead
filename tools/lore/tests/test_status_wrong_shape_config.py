"""Regression: a well-formed-JSON-but-wrong-shape ``config.json`` must degrade
cleanly for every ``_resolve_all_vaults`` caller, not just ``lore areas``.

Round one widened ``_load_vault_config``/``_resolve_all_vaults``'s except
tuples to include ``AttributeError``/``TypeError`` so a wrong-shape config
(e.g. a top-level list) wouldn't raise out of ``load_config``'s ``.get()``
calls. Round two normalized that raise to ``VaultConfigError`` inside
``validate_config`` (the module's own parse+validate boundary) and narrowed
the tuples back — every existing wrong-shape test exercised only ``lore
areas``, so this pins a second, independent caller: ``lore status``'s
``_report_vault_drift``.
"""

from __future__ import annotations

import io
import json
from unittest import mock


def _load_init():
    import importlib
    from lore.cli import init
    return importlib.reload(init)


def test_report_vault_drift_prints_clean_line_on_top_level_list_config(tmp_path, monkeypatch):
    config_home = tmp_path / "_xdg_config"
    state_home = tmp_path / "_xdg_state"
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True)
    (lore_cfg / "config.json").write_text(json.dumps([]), encoding="utf-8")

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))

    init_mod = _load_init()
    err = io.StringIO()
    with mock.patch("sys.stderr", err):
        init_mod._report_vault_drift()

    output = err.getvalue()
    assert "Traceback" not in output
    assert output.strip() != ""
    assert "lore: vault config unreadable" in output
    assert len(output.strip().splitlines()) == 1


def test_report_vault_drift_prints_clean_line_on_non_dict_vault_entries(tmp_path, monkeypatch):
    config_home = tmp_path / "_xdg_config"
    state_home = tmp_path / "_xdg_state"
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True)
    (lore_cfg / "config.json").write_text(
        json.dumps({"vaults": ["default"]}), encoding="utf-8"
    )

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))

    init_mod = _load_init()
    err = io.StringIO()
    with mock.patch("sys.stderr", err):
        init_mod._report_vault_drift()

    output = err.getvalue()
    assert "Traceback" not in output
    assert output.strip() != ""
    assert "lore: vault config unreadable" in output
    assert len(output.strip().splitlines()) == 1
