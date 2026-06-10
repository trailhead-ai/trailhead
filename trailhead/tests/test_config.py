"""Tests for trailhead/config.py — TrailheadConfig dataclass + load/save.

TDD: these tests are written BEFORE the implementation. All must pass after
trailhead/config.py is implemented.

Hermeticity contract (the Step-4 lesson):
  Every test that touches config paths MUST use tmp_path + the
  TRAILHEAD_CONFIG_DIR env override. NO test may read or write the
  real config_dir("trailhead") or ~/.config/trailhead.
"""

import stat
import tomllib
from pathlib import Path

import pytest

from trailhead.config import ConfigError, TrailheadConfig, load_config, save_config

_DEFAULT_REGISTRY = "github.com/trailhead-ai"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_env(tmp_path: Path) -> dict[str, str]:
    """Return a minimal env dict that redirects config_dir("trailhead") to tmp_path."""
    return {"TRAILHEAD_CONFIG_DIR": str(tmp_path), "HOME": str(tmp_path)}


# ---------------------------------------------------------------------------
# Default config (absent file)
# ---------------------------------------------------------------------------


class TestDefaultConfig:
    def test_absent_file_returns_default_no_crash(self, tmp_path):
        env = _make_env(tmp_path)
        cfg = load_config(env=env)
        assert isinstance(cfg, TrailheadConfig)

    def test_default_preset_is_standard(self, tmp_path):
        env = _make_env(tmp_path)
        cfg = load_config(env=env)
        assert cfg.preset == "standard"

    def test_default_path_integration_is_true(self, tmp_path):
        env = _make_env(tmp_path)
        cfg = load_config(env=env)
        assert cfg.path_integration is True

    def test_default_registry(self, tmp_path):
        env = _make_env(tmp_path)
        cfg = load_config(env=env)
        assert cfg.registry == _DEFAULT_REGISTRY

    def test_default_capabilities_is_empty_dict(self, tmp_path):
        env = _make_env(tmp_path)
        cfg = load_config(env=env)
        assert cfg.capabilities == {}


# ---------------------------------------------------------------------------
# Round-trip: save → load
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_roundtrip_preserves_preset(self, tmp_path):
        env = _make_env(tmp_path)
        cfg = TrailheadConfig(
            registry=_DEFAULT_REGISTRY,
            path_integration=True,
            preset="minimal",
            capabilities={},
        )
        save_config(cfg, env=env)
        loaded = load_config(env=env)
        assert loaded.preset == "minimal"

    def test_roundtrip_preserves_path_integration_false(self, tmp_path):
        env = _make_env(tmp_path)
        cfg = TrailheadConfig(
            registry=_DEFAULT_REGISTRY,
            path_integration=False,
            preset="standard",
            capabilities={},
        )
        save_config(cfg, env=env)
        loaded = load_config(env=env)
        assert loaded.path_integration is False

    def test_roundtrip_preserves_registry(self, tmp_path):
        env = _make_env(tmp_path)
        custom_registry = "github.example-corp.internal/trailhead"
        cfg = TrailheadConfig(
            registry=custom_registry,
            path_integration=True,
            preset="standard",
            capabilities={},
        )
        save_config(cfg, env=env)
        loaded = load_config(env=env)
        assert loaded.registry == custom_registry

    def test_roundtrip_preserves_capabilities(self, tmp_path):
        env = _make_env(tmp_path)
        caps = {"lore": ["capture", "recall"], "forge": ["planning"]}
        cfg = TrailheadConfig(
            registry=_DEFAULT_REGISTRY,
            path_integration=True,
            preset="standard",
            capabilities=caps,
        )
        save_config(cfg, env=env)
        loaded = load_config(env=env)
        assert loaded.capabilities == caps

    def test_roundtrip_byte_faithful(self, tmp_path):
        """Save twice and confirm the file content is identical (deterministic serialization)."""
        env = _make_env(tmp_path)
        cfg = TrailheadConfig(
            registry=_DEFAULT_REGISTRY,
            path_integration=True,
            preset="full",
            capabilities={"lore": ["capture"]},
        )
        save_config(cfg, env=env)
        config_file = tmp_path / "config.toml"
        first_bytes = config_file.read_bytes()
        save_config(cfg, env=env)
        second_bytes = config_file.read_bytes()
        assert first_bytes == second_bytes


# ---------------------------------------------------------------------------
# Config path hermeticity and dir creation
# ---------------------------------------------------------------------------


class TestConfigPath:
    def test_config_path_under_env_override(self, tmp_path):
        """Config file lands under TRAILHEAD_CONFIG_DIR, not the real config dir."""
        env = _make_env(tmp_path)
        cfg = TrailheadConfig(
            registry=_DEFAULT_REGISTRY,
            path_integration=True,
            preset="standard",
            capabilities={},
        )
        save_config(cfg, env=env)
        assert (tmp_path / "config.toml").exists()

    def test_config_dir_created_with_0o700(self, tmp_path):
        """The config dir is created with mode 0o700."""
        config_subdir = tmp_path / "trailhead_config"
        env = {"TRAILHEAD_CONFIG_DIR": str(config_subdir), "HOME": str(tmp_path)}
        cfg = TrailheadConfig(
            registry=_DEFAULT_REGISTRY,
            path_integration=True,
            preset="standard",
            capabilities={},
        )
        save_config(cfg, env=env)
        mode = stat.S_IMODE(config_subdir.stat().st_mode)
        assert mode == 0o700

    def test_different_env_dir_uses_that_dir(self, tmp_path):
        """Two different env overrides produce config files in their respective dirs."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        env_a = {"TRAILHEAD_CONFIG_DIR": str(dir_a), "HOME": str(tmp_path)}
        env_b = {"TRAILHEAD_CONFIG_DIR": str(dir_b), "HOME": str(tmp_path)}
        cfg = TrailheadConfig(
            registry=_DEFAULT_REGISTRY,
            path_integration=True,
            preset="minimal",
            capabilities={},
        )
        save_config(cfg, env=env_a)
        save_config(cfg, env=env_b)
        assert (dir_a / "config.toml").exists()
        assert (dir_b / "config.toml").exists()


# ---------------------------------------------------------------------------
# Malformed TOML → ConfigError (named, cites the file)
# ---------------------------------------------------------------------------


class TestMalformedToml:
    def test_malformed_toml_raises_config_error(self, tmp_path):
        env = _make_env(tmp_path)
        config_file = tmp_path / "config.toml"
        config_file.write_text("this is not valid toml [[[")
        with pytest.raises(ConfigError) as exc_info:
            load_config(env=env)
        assert "config.toml" in str(exc_info.value)

    def test_config_error_is_not_raw_toml_error(self, tmp_path):
        env = _make_env(tmp_path)
        config_file = tmp_path / "config.toml"
        config_file.write_text("x = [[[")
        with pytest.raises(ConfigError):
            load_config(env=env)
        # Confirm it's NOT a raw TOMLDecodeError leaking through
        try:
            load_config(env=env)
        except ConfigError:
            pass
        except tomllib.TOMLDecodeError:
            pytest.fail("raw TOMLDecodeError leaked through; must be wrapped in ConfigError")

    def test_config_error_message_cites_file_path(self, tmp_path):
        env = _make_env(tmp_path)
        config_file = tmp_path / "config.toml"
        config_file.write_text("registry = [invalid")
        with pytest.raises(ConfigError) as exc_info:
            load_config(env=env)
        assert str(tmp_path) in str(exc_info.value)
