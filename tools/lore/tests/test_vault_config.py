"""Tests for the vault config model — ``vault_config.py``.

Covers the test contract:

- A valid multi-vault config parses to the expected Vault list; default path
  derives under a tmp ``$XDG_STATE_HOME``; an explicit path is honored.
- Normalization: a repo vault added as ``trailhead-ai/trailhead`` is stored as
  ``trailhead-ai_trailhead``; its default path is a confined single segment under
  ``vaults/``; ``normalize_vault_name`` is idempotent.
- Hard errors (each raises a named exception): zero default vaults; two default
  vaults; a default vault with a records allowlist; a default vault with
  ``shared: true``; duplicate name across scopes (compared normalized); bad scope
  value; a records entry not in ``record_model.KINDS``; a name that is empty or
  ``..`` after normalization.
- ``is_shared``: no ``shared`` key → False; ``shared: true`` → True; multiple
  non-default vaults can all be False.
- ``is_configured_vault`` true/false against the loaded set.
"""

import json

import pytest

from conftest import load_script


def vc():
    return load_script("vault_config")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path, data: dict) -> str:
    """Write a config dict to a tmp config.json, return the path string."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return str(p)


def _minimal_config(extra_vaults=None) -> dict:
    """Return a minimal valid config with one default vault."""
    vaults = [{"name": "default", "scope": "default"}]
    if extra_vaults:
        vaults.extend(extra_vaults)
    return {"vaults": vaults}


# ---------------------------------------------------------------------------
# 1. Valid multi-vault config parses correctly
# ---------------------------------------------------------------------------


def test_parse_minimal_config_returns_one_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config_path = _write_config(tmp_path, _minimal_config())
    vaults = cfg.load_config(config_path)
    assert len(vaults) == 1
    v = vaults[0]
    assert v.name == "default"
    assert v.scope == "default"
    assert v.shared is False


def test_parse_multi_vault_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    data = _minimal_config(
        extra_vaults=[
            {"name": "trailhead-ai_trailhead", "scope": "repo", "records": ["spec", "plan"]},
            {"name": "product-engineering", "scope": "team", "records": ["blob"]},
        ]
    )
    config_path = _write_config(tmp_path, data)
    vaults = cfg.load_config(config_path)
    assert len(vaults) == 3
    names = {v.name for v in vaults}
    assert names == {"default", "trailhead-ai_trailhead", "product-engineering"}


def test_default_path_derives_under_xdg_state(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    cfg = vc()
    config_path = _write_config(tmp_path, _minimal_config())
    vaults = cfg.load_config(config_path)
    default_vault = vaults[0]
    # Path must be under state/lore/vaults/default
    assert str(default_vault.path).startswith(str(state))
    assert "vaults" in str(default_vault.path)
    assert str(default_vault.path).endswith("default")


def test_explicit_path_is_honored(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    explicit = tmp_path / "my-vault"
    explicit.mkdir(parents=True)
    cfg = vc()
    data = {"vaults": [{"name": "default", "scope": "default", "path": str(explicit)}]}
    config_path = _write_config(tmp_path, data)
    vaults = cfg.load_config(config_path)
    assert str(vaults[0].path) == str(explicit.resolve())


def test_vault_has_expected_fields(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    cfg = vc()
    data = _minimal_config(
        extra_vaults=[
            {"name": "my-team", "scope": "team", "records": ["spec"], "shared": True},
        ]
    )
    config_path = _write_config(tmp_path, data)
    vaults = cfg.load_config(config_path)
    team_vault = next(v for v in vaults if v.name == "my-team")
    assert team_vault.scope == "team"
    assert team_vault.records == ["spec"]
    assert team_vault.shared is True


def test_vault_records_defaults_to_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config_path = _write_config(tmp_path, _minimal_config())
    vaults = cfg.load_config(config_path)
    assert vaults[0].records == []


# ---------------------------------------------------------------------------
# 2. Normalization
# ---------------------------------------------------------------------------


def test_normalize_vault_name_replaces_slash(tmp_path):
    cfg = vc()
    assert cfg.normalize_vault_name("trailhead-ai/trailhead") == "trailhead-ai_trailhead"


def test_normalize_vault_name_idempotent(tmp_path):
    cfg = vc()
    normalized = cfg.normalize_vault_name("trailhead-ai/trailhead")
    assert cfg.normalize_vault_name(normalized) == normalized


def test_normalize_vault_name_no_slash_unchanged():
    cfg = vc()
    assert cfg.normalize_vault_name("my-vault") == "my-vault"


def test_repo_vault_with_slash_stored_as_normalized(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    cfg = vc()
    # The config already stores the normalized name (normalization happens at
    # add time). When stored as trailhead-ai/trailhead in config, the
    # load_config should normalize it on load.
    data = {
        "vaults": [
            {"name": "default", "scope": "default"},
            {"name": "trailhead-ai/trailhead", "scope": "repo"},
        ]
    }
    config_path = _write_config(tmp_path, data)
    vaults = cfg.load_config(config_path)
    repo_vault = next(v for v in vaults if v.scope == "repo")
    assert repo_vault.name == "trailhead-ai_trailhead"


def test_repo_vault_default_path_is_single_segment_under_vaults(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    cfg = vc()
    data = {
        "vaults": [
            {"name": "default", "scope": "default"},
            {"name": "trailhead-ai/trailhead", "scope": "repo"},
        ]
    }
    config_path = _write_config(tmp_path, data)
    vaults = cfg.load_config(config_path)
    repo_vault = next(v for v in vaults if v.scope == "repo")
    path_str = str(repo_vault.path)
    # Should end with vaults/trailhead-ai_trailhead — a single path segment, no nesting
    assert path_str.endswith("trailhead-ai_trailhead")
    # Parent should be the vaults dir, not a nested org dir
    from pathlib import Path

    assert Path(path_str).parent.name == "vaults"


# ---------------------------------------------------------------------------
# 3. Hard errors
# ---------------------------------------------------------------------------


def test_zero_default_vaults_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    data = {"vaults": [{"name": "my-team", "scope": "team"}]}
    config_path = _write_config(tmp_path, data)
    with pytest.raises(cfg.VaultConfigError):
        cfg.load_config(config_path)


def test_two_default_vaults_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    data = {
        "vaults": [
            {"name": "default", "scope": "default"},
            {"name": "default2", "scope": "default"},
        ]
    }
    config_path = _write_config(tmp_path, data)
    with pytest.raises(cfg.VaultConfigError):
        cfg.load_config(config_path)


def test_default_vault_with_records_allowlist_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    data = {"vaults": [{"name": "default", "scope": "default", "records": ["spec"]}]}
    config_path = _write_config(tmp_path, data)
    with pytest.raises(cfg.VaultConfigError):
        cfg.load_config(config_path)


def test_default_vault_with_shared_true_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    data = {"vaults": [{"name": "default", "scope": "default", "shared": True}]}
    config_path = _write_config(tmp_path, data)
    with pytest.raises(cfg.VaultConfigError):
        cfg.load_config(config_path)


def test_duplicate_name_across_scopes_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    data = {
        "vaults": [
            {"name": "default", "scope": "default"},
            {"name": "my-vault", "scope": "team"},
            {"name": "my-vault", "scope": "product"},
        ]
    }
    config_path = _write_config(tmp_path, data)
    with pytest.raises(cfg.VaultConfigError):
        cfg.load_config(config_path)


def test_duplicate_name_after_normalization_raises(tmp_path, monkeypatch):
    """Two vaults whose names normalize to the same string are a hard error."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    # "org/repo" and "org_repo" normalize to the same "org_repo"
    data = {
        "vaults": [
            {"name": "default", "scope": "default"},
            {"name": "org/repo", "scope": "repo"},
            {"name": "org_repo", "scope": "product"},
        ]
    }
    config_path = _write_config(tmp_path, data)
    with pytest.raises(cfg.VaultConfigError):
        cfg.load_config(config_path)


def test_bad_scope_value_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    data = {
        "vaults": [
            {"name": "default", "scope": "default"},
            {"name": "my-vault", "scope": "workspace"},  # invalid scope
        ]
    }
    config_path = _write_config(tmp_path, data)
    with pytest.raises(cfg.VaultConfigError):
        cfg.load_config(config_path)


def test_records_entry_not_in_kinds_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    data = {
        "vaults": [
            {"name": "default", "scope": "default"},
            {"name": "my-vault", "scope": "team", "records": ["nosuchkind"]},
        ]
    }
    config_path = _write_config(tmp_path, data)
    with pytest.raises(cfg.VaultConfigError):
        cfg.load_config(config_path)


def test_empty_name_after_normalization_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    data = {
        "vaults": [
            {"name": "default", "scope": "default"},
            {"name": "", "scope": "team"},
        ]
    }
    config_path = _write_config(tmp_path, data)
    with pytest.raises(cfg.VaultConfigError):
        cfg.load_config(config_path)


def test_dotdot_name_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    data = {
        "vaults": [
            {"name": "default", "scope": "default"},
            {"name": "..", "scope": "team"},
        ]
    }
    config_path = _write_config(tmp_path, data)
    with pytest.raises(cfg.VaultConfigError):
        cfg.load_config(config_path)


def test_error_message_names_violation(tmp_path, monkeypatch):
    """VaultConfigError messages should name the specific violation."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    data = {"vaults": [{"name": "default", "scope": "default", "shared": True}]}
    config_path = _write_config(tmp_path, data)
    with pytest.raises(cfg.VaultConfigError) as exc_info:
        cfg.load_config(config_path)
    assert "shared" in str(exc_info.value).lower() or "default" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 4. is_shared
# ---------------------------------------------------------------------------


def test_is_shared_no_key_returns_false(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config_path = _write_config(
        tmp_path,
        _minimal_config(
            extra_vaults=[
                {"name": "my-team", "scope": "team"},
            ]
        ),
    )
    vaults = cfg.load_config(config_path)
    team_vault = next(v for v in vaults if v.name == "my-team")
    assert cfg.is_shared(team_vault) is False


def test_is_shared_explicit_true_returns_true(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config_path = _write_config(
        tmp_path,
        _minimal_config(
            extra_vaults=[
                {"name": "shared-team", "scope": "team", "shared": True},
            ]
        ),
    )
    vaults = cfg.load_config(config_path)
    team_vault = next(v for v in vaults if v.name == "shared-team")
    assert cfg.is_shared(team_vault) is True


def test_is_shared_multiple_non_default_can_all_be_false(tmp_path, monkeypatch):
    """No singleton 'personal' vault — multiple vaults can be unshared."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config_path = _write_config(
        tmp_path,
        _minimal_config(
            extra_vaults=[
                {"name": "team-a", "scope": "team"},
                {"name": "my-repo", "scope": "repo"},
            ]
        ),
    )
    vaults = cfg.load_config(config_path)
    non_default = [v for v in vaults if v.scope != "default"]
    assert len(non_default) == 2
    assert all(not cfg.is_shared(v) for v in non_default)


def test_is_shared_default_vault_always_false(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config_path = _write_config(tmp_path, _minimal_config())
    vaults = cfg.load_config(config_path)
    default_vault = next(v for v in vaults if v.scope == "default")
    assert cfg.is_shared(default_vault) is False


# ---------------------------------------------------------------------------
# 5. is_configured_vault
# ---------------------------------------------------------------------------


def test_is_configured_vault_true_for_present_name(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config_path = _write_config(
        tmp_path,
        _minimal_config(
            extra_vaults=[
                {"name": "my-team", "scope": "team"},
            ]
        ),
    )
    vaults = cfg.load_config(config_path)
    assert cfg.is_configured_vault("my-team", vaults) is True


def test_is_configured_vault_false_for_absent_name(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config_path = _write_config(tmp_path, _minimal_config())
    vaults = cfg.load_config(config_path)
    assert cfg.is_configured_vault("nonexistent", vaults) is False


def test_is_configured_vault_matches_after_normalization(tmp_path, monkeypatch):
    """is_configured_vault normalizes the query name before comparing."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    # Config stores normalized name trailhead-ai_trailhead (normalized on load)
    data = {
        "vaults": [
            {"name": "default", "scope": "default"},
            {"name": "trailhead-ai/trailhead", "scope": "repo"},
        ]
    }
    config_path = _write_config(tmp_path, data)
    vaults = cfg.load_config(config_path)
    # Both the normalized and un-normalized form should match
    assert cfg.is_configured_vault("trailhead-ai_trailhead", vaults) is True
    assert cfg.is_configured_vault("trailhead-ai/trailhead", vaults) is True


def test_is_configured_vault_true_for_default(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config_path = _write_config(tmp_path, _minimal_config())
    vaults = cfg.load_config(config_path)
    assert cfg.is_configured_vault("default", vaults) is True
