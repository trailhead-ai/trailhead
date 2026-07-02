"""Tests for the config mutation API + atomic JSON write.

Covers the test contract:

- Round-trip: load ``config.json`` → ``add_vault_entry`` → ``write_config_atomic``
  → re-read; new entry is present and existing entries are intact; result
  re-parses to expected structure via ``load_config``.
- Special-char fidelity: a name/path containing ``"``, ``\\``, ``/`` round-trips
  correctly (json escaping — no syntax drift).
- ``remove_vault_entry`` removes exactly the named entry, leaves siblings intact.
- add-then-remove returns a structurally-equivalent config (idempotent round-trip).
- Atomic write: a write whose verify-reparse fails leaves ``config.json``
  UNCHANGED (original is never clobbered with a broken file).
"""

import json
from pathlib import Path

import pytest

from conftest import load_script


def vc():
    return load_script("lore.vault.config")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path, data: dict) -> "Path":
    """Write a config dict to a tmp config.json; return the Path."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return p


def _minimal_config(extra_vaults=None) -> dict:
    """Return a minimal valid config dict with one default vault."""
    vaults = [{"name": "default", "scope": "default"}]
    if extra_vaults:
        vaults.extend(extra_vaults)
    return {"vaults": vaults}


def _read_config(path) -> dict:
    """Read and parse a config.json, return the raw dict."""
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Round-trip: add_vault_entry + write_config_atomic + re-read
# ---------------------------------------------------------------------------


def test_add_vault_entry_appends_to_vaults_array(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config = _minimal_config()
    new_entry = {"name": "my-team", "scope": "team"}
    cfg.add_vault_entry(config, new_entry)
    assert len(config["vaults"]) == 2
    assert config["vaults"][-1] == new_entry


def test_add_vault_entry_preserves_existing_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config = _minimal_config(extra_vaults=[{"name": "existing", "scope": "team"}])
    original_first = config["vaults"][0].copy()
    original_second = config["vaults"][1].copy()
    cfg.add_vault_entry(config, {"name": "new-vault", "scope": "repo"})
    assert config["vaults"][0] == original_first
    assert config["vaults"][1] == original_second


def test_write_config_atomic_round_trip(tmp_path, monkeypatch):
    """load → add_vault_entry → write_config_atomic → re-read has new entry."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config_path = _write_config(tmp_path, _minimal_config())
    # Load the raw config dict
    config = _read_config(config_path)
    # Add a new entry
    cfg.add_vault_entry(config, {"name": "team-eng", "scope": "team"})
    # Atomic write
    cfg.write_config_atomic(config_path, config)
    # Re-read and assert
    after = _read_config(config_path)
    names = [v["name"] for v in after["vaults"]]
    assert "default" in names
    assert "team-eng" in names


def test_write_config_atomic_result_parseable_by_load_config(tmp_path, monkeypatch):
    """After write, the file is re-parseable by load_config (valid structure)."""
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    cfg = vc()
    config_path = _write_config(tmp_path, _minimal_config())
    config = _read_config(config_path)
    cfg.add_vault_entry(config, {"name": "product", "scope": "product"})
    cfg.write_config_atomic(config_path, config)
    # load_config should not raise
    vaults = cfg.load_config(str(config_path))
    names = {v.name for v in vaults}
    assert "default" in names
    assert "product" in names


def test_write_config_atomic_existing_entries_intact_after_add(tmp_path, monkeypatch):
    """Existing entry data is preserved byte-for-byte through write/re-read."""
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    cfg = vc()
    original_team = {"name": "team-a", "scope": "team", "records": ["spec", "plan"]}
    config_path = _write_config(tmp_path, _minimal_config(extra_vaults=[original_team]))
    config = _read_config(config_path)
    cfg.add_vault_entry(config, {"name": "team-b", "scope": "team"})
    cfg.write_config_atomic(config_path, config)
    after = _read_config(config_path)
    team_a = next(v for v in after["vaults"] if v["name"] == "team-a")
    assert team_a == original_team


# ---------------------------------------------------------------------------
# 2. Special-char fidelity
# ---------------------------------------------------------------------------


def test_special_chars_in_name_roundtrip(tmp_path, monkeypatch):
    """A name containing \", \\, / survives json encode/decode with no drift."""
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    cfg = vc()
    # We use a raw name; the mutation API stores it as-is in the dict
    # (normalization is applied at add-command time)
    tricky_name = 'team-"x\\_y/z'
    config_path = _write_config(tmp_path, _minimal_config())
    config = _read_config(config_path)
    cfg.add_vault_entry(config, {"name": tricky_name, "scope": "team"})
    cfg.write_config_atomic(config_path, config)
    after = _read_config(config_path)
    stored_name = next(v["name"] for v in after["vaults"] if v["name"] == tricky_name)
    assert stored_name == tricky_name


def test_special_chars_in_path_roundtrip(tmp_path, monkeypatch):
    """A path containing \", \\, / survives json encode/decode with no drift."""
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    cfg = vc()
    # Use an absolute path that contains special chars in some component
    tricky_path = str(tmp_path / "vaults" / 'my-"vault')
    config_path = _write_config(tmp_path, _minimal_config())
    config = _read_config(config_path)
    cfg.add_vault_entry(config, {"name": "special", "scope": "team", "path": tricky_path})
    cfg.write_config_atomic(config_path, config)
    after = _read_config(config_path)
    entry = next(v for v in after["vaults"] if v.get("name") == "special")
    assert entry["path"] == tricky_path


# ---------------------------------------------------------------------------
# 3. remove_vault_entry removes exactly one entry, leaves siblings
# ---------------------------------------------------------------------------


def test_remove_vault_entry_removes_named_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config = _minimal_config(
        extra_vaults=[
            {"name": "team-a", "scope": "team"},
            {"name": "team-b", "scope": "team"},
        ]
    )
    cfg.remove_vault_entry(config, "team-a")
    names = [v["name"] for v in config["vaults"]]
    assert "team-a" not in names


def test_remove_vault_entry_leaves_siblings_intact(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config = _minimal_config(
        extra_vaults=[
            {"name": "team-a", "scope": "team"},
            {"name": "team-b", "scope": "team"},
        ]
    )
    cfg.remove_vault_entry(config, "team-a")
    names = [v["name"] for v in config["vaults"]]
    assert "team-b" in names
    assert "default" in names
    assert len(names) == 2


def test_remove_vault_entry_normalizes_name(tmp_path, monkeypatch):
    """remove_vault_entry matches on normalized name, consistent with module convention."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    # Store a name with / in the raw config (pre-normalization)
    config = _minimal_config(
        extra_vaults=[
            {"name": "org/repo", "scope": "repo"},
        ]
    )
    # Remove using either form — both should match "org_repo" normalized
    cfg.remove_vault_entry(config, "org/repo")
    names = [v["name"] for v in config["vaults"]]
    assert "org/repo" not in names


def test_remove_vault_entry_by_normalized_form(tmp_path, monkeypatch):
    """remove_vault_entry accepts the already-normalized form too."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config = _minimal_config(
        extra_vaults=[
            {"name": "org_repo", "scope": "repo"},
        ]
    )
    cfg.remove_vault_entry(config, "org_repo")
    names = [v["name"] for v in config["vaults"]]
    assert "org_repo" not in names


def test_remove_vault_entry_returns_none_on_missing_name(tmp_path, monkeypatch):
    """Removing a non-existent name is a no-op (does not raise)."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config = _minimal_config()
    original_count = len(config["vaults"])
    cfg.remove_vault_entry(config, "nonexistent")
    assert len(config["vaults"]) == original_count


# ---------------------------------------------------------------------------
# 4. add-then-remove idempotent round-trip
# ---------------------------------------------------------------------------


def test_add_then_remove_returns_equivalent_config(tmp_path, monkeypatch):
    """add_vault_entry then remove_vault_entry leaves the config structurally equivalent."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config = _minimal_config(extra_vaults=[{"name": "product", "scope": "product"}])
    original_vaults = [v.copy() for v in config["vaults"]]
    cfg.add_vault_entry(config, {"name": "temp-vault", "scope": "team"})
    cfg.remove_vault_entry(config, "temp-vault")
    assert config["vaults"] == original_vaults


def test_add_then_remove_persisted_round_trip(tmp_path, monkeypatch):
    """Write after add, then write again after remove — config.json recovers."""
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    cfg = vc()
    original_data = _minimal_config(extra_vaults=[{"name": "product", "scope": "product"}])
    config_path = _write_config(tmp_path, original_data)
    # Add and persist
    config = _read_config(config_path)
    cfg.add_vault_entry(config, {"name": "temp-vault", "scope": "team"})
    cfg.write_config_atomic(config_path, config)
    # Remove and persist
    config = _read_config(config_path)
    cfg.remove_vault_entry(config, "temp-vault")
    cfg.write_config_atomic(config_path, config)
    # Final state must match original
    after = _read_config(config_path)
    assert after == original_data


# ---------------------------------------------------------------------------
# 5. Atomic write safety: verify failure leaves config.json unchanged
# ---------------------------------------------------------------------------


def test_atomic_write_failure_leaves_config_unchanged(tmp_path, monkeypatch):
    """If verify-reparse fails, config.json is left byte-for-byte unchanged."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    original_data = _minimal_config()
    config_path = _write_config(tmp_path, original_data)
    original_bytes = config_path.read_bytes()

    # Monkeypatch json.load inside the vault_config module to raise on re-read,
    # simulating a verify failure after the temp file is written.
    import json as stdlib_json

    def fail_on_load(fp):
        raise ValueError("simulated verify failure")

    # Patch the json module attribute on the loaded vault_config module object
    fake_json = type(
        "FakeJson",
        (),
        {
            "dump": staticmethod(stdlib_json.dump),
            "load": staticmethod(fail_on_load),
            "loads": staticmethod(stdlib_json.loads),
            "JSONDecodeError": stdlib_json.JSONDecodeError,
        },
    )()
    monkeypatch.setattr(cfg, "json", fake_json)

    # write_config_atomic must exist and raise on verify failure
    assert hasattr(cfg, "write_config_atomic"), "write_config_atomic must be implemented"
    with pytest.raises(Exception, match="simulated verify failure"):
        cfg.write_config_atomic(config_path, {"vaults": [{"name": "default", "scope": "default"}]})

    # config.json must be byte-for-byte unchanged
    assert config_path.read_bytes() == original_bytes


def test_atomic_write_failure_cleans_up_temp_file(tmp_path, monkeypatch):
    """After verify failure, no temp file is left behind in the config directory."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = vc()
    config_path = _write_config(tmp_path, _minimal_config())

    import json as stdlib_json

    def fail_on_load(fp):
        raise ValueError("simulated verify failure")

    fake_json = type(
        "FakeJson",
        (),
        {
            "dump": staticmethod(stdlib_json.dump),
            "load": staticmethod(fail_on_load),
            "loads": staticmethod(stdlib_json.loads),
            "JSONDecodeError": stdlib_json.JSONDecodeError,
        },
    )()
    monkeypatch.setattr(cfg, "json", fake_json)

    with pytest.raises(Exception, match="simulated verify failure"):
        cfg.write_config_atomic(config_path, _minimal_config())

    # No .tmp files should remain
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"temp files left behind: {tmp_files}"
