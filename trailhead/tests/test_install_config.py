"""Tests for trailhead/install_config.py — pure config resolution."""

from pathlib import Path

import pytest

from trailhead.capabilities import load_manifest
from trailhead.compose import UnknownSkillError, UnknownSubagentError
from trailhead.install_config import (
    ConfigResolveError,
    ResolvedConfig,
    resolve_config,
    resolve_config_path,
)
from trailhead.wire import default_manifest_paths

_REPO_ROOT = Path(__file__).parent.parent.parent
_MANIFESTS = default_manifest_paths()


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "cfg.toml"
    p.write_text(content)
    return p


def _lore_inv():
    return load_manifest(_MANIFESTS["lore"])


# ---------------------------------------------------------------------------
# resolve_config_path
# ---------------------------------------------------------------------------


class TestConfigPath:
    def test_default_is_config_default_toml(self):
        assert resolve_config_path(None, Path("/repo")) == Path("/repo/config/default.toml")

    def test_relative_resolves_under_config_dir(self):
        assert resolve_config_path("mine.toml", Path("/repo")) == Path("/repo/config/mine.toml")

    def test_absolute_respected(self):
        assert resolve_config_path("/etc/x.toml", Path("/repo")) == Path("/etc/x.toml")


# ---------------------------------------------------------------------------
# CLI flags / defaults
# ---------------------------------------------------------------------------


class TestFlagsAndDefaults:
    def test_defaults_true_with_no_config(self):
        cfg = resolve_config(detected_harnesses=["claude_code"])
        assert isinstance(cfg, ResolvedConfig)
        assert cfg.install_camp_cli is True
        assert cfg.install_lore_cli is True

    def test_no_camp_flag(self):
        cfg = resolve_config(detected_harnesses=["claude_code"], no_camp=True)
        assert cfg.install_camp_cli is False
        assert cfg.install_lore_cli is True

    def test_no_lore_flag(self):
        cfg = resolve_config(detected_harnesses=["claude_code"], no_lore=True)
        assert cfg.install_lore_cli is False

    def test_config_can_set_install_flags(self, tmp_path):
        path = _write(tmp_path, "install_camp_cli = true\ninstall_lore_cli = false\nplugins=[]\n")
        cfg = resolve_config(config_path=path, detected_harnesses=["claude_code"])
        assert cfg.install_camp_cli is True
        assert cfg.install_lore_cli is False

    def test_cli_no_lore_overrides_config_true(self, tmp_path):
        path = _write(tmp_path, "install_lore_cli = true\nplugins=[]\n")
        cfg = resolve_config(config_path=path, detected_harnesses=["claude_code"], no_lore=True)
        assert cfg.install_lore_cli is False


# ---------------------------------------------------------------------------
# Harness resolution + precedence
# ---------------------------------------------------------------------------


class TestHarnessResolution:
    def test_detected_used_when_no_cli_or_config(self):
        cfg = resolve_config(detected_harnesses=["claude_code"])
        assert [h.name for h in cfg.harnesses] == ["claude_code"]

    def test_cli_alias_canonicalized(self):
        cfg = resolve_config(cli_harnesses=["claude"], detected_harnesses=[])
        assert [h.name for h in cfg.harnesses] == ["claude_code"]

    def test_cli_overrides_detection(self):
        cfg = resolve_config(cli_harnesses=["claude_code"], detected_harnesses=[])
        assert [h.name for h in cfg.harnesses] == ["claude_code"]

    def test_config_blocks_used_when_no_cli(self, tmp_path):
        path = _write(tmp_path, '[[harness]]\nname="claude_code"\nplugins=["lore"]\n')
        cfg = resolve_config(config_path=path, detected_harnesses=[])
        assert [h.name for h in cfg.harnesses] == ["claude_code"]

    def test_no_harness_anywhere_yields_empty(self):
        cfg = resolve_config(detected_harnesses=[])
        assert cfg.harnesses == []

    def test_unknown_harness_raises(self):
        with pytest.raises(ConfigResolveError, match="codex"):
            resolve_config(cli_harnesses=["codex"], detected_harnesses=[])


# ---------------------------------------------------------------------------
# Plugin expansion
# ---------------------------------------------------------------------------


class TestPluginExpansion:
    def test_string_plugin_expands_to_all(self):
        cfg = resolve_config(cli_harnesses=["claude_code"], cli_plugins=["lore"])
        plugin = cfg.harnesses[0].plugins[0]
        inv = _lore_inv()
        assert set(plugin.subagents) == set(inv.subagents)
        assert set(plugin.skills) == set(inv.skills)
        assert all(v is None for v in plugin.subagents.values())
        assert all(v is None for v in plugin.skills.values())

    def test_top_level_plugins_default(self, tmp_path):
        path = _write(tmp_path, 'plugins = ["lore", "camp"]\n')
        cfg = resolve_config(config_path=path, detected_harnesses=["claude_code"])
        assert [p.name for p in cfg.harnesses[0].plugins] == ["lore", "camp"]

    def test_no_plugins_anywhere_defaults_to_all_known(self, tmp_path):
        path = _write(tmp_path, "install_camp_cli = true\n")  # no plugins key
        cfg = resolve_config(config_path=path, detected_harnesses=["claude_code"])
        names = [p.name for p in cfg.harnesses[0].plugins]
        assert names == ["camp", "lore", "craft", "portage", "outpost"]

    def test_per_harness_plugins_override_top_level(self, tmp_path):
        path = _write(
            tmp_path,
            'plugins = ["lore"]\n[[harness]]\nname="claude_code"\nplugins=["camp"]\n',
        )
        cfg = resolve_config(config_path=path, detected_harnesses=[])
        assert [p.name for p in cfg.harnesses[0].plugins] == ["camp"]

    def test_cli_plugins_replace(self, tmp_path):
        path = _write(tmp_path, 'plugins = ["lore", "camp", "craft"]\n')
        cfg = resolve_config(
            config_path=path, detected_harnesses=["claude_code"], cli_plugins=["lore"]
        )
        assert [p.name for p in cfg.harnesses[0].plugins] == ["lore"]

    def test_map_form_with_named_subset(self, tmp_path):
        path = _write(
            tmp_path,
            '[[harness]]\nname="claude_code"\n'
            '  [[harness.plugins]]\n  name="craft"\n'
            '  subagents=["advocate","artist"]\n  skills=["execute"]\n',
        )
        cfg = resolve_config(config_path=path, detected_harnesses=[])
        craft = cfg.harnesses[0].plugins[0]
        assert set(craft.subagents) == {"advocate", "artist"}
        assert set(craft.skills) == {"execute"}

    def test_map_form_missing_keys_means_all(self, tmp_path):
        path = _write(
            tmp_path,
            '[[harness]]\nname="claude_code"\n  [[harness.plugins]]\n  name="lore"\n',
        )
        cfg = resolve_config(config_path=path, detected_harnesses=[])
        lore = cfg.harnesses[0].plugins[0]
        inv = _lore_inv()
        assert set(lore.subagents) == set(inv.subagents)
        assert set(lore.skills) == set(inv.skills)

    def test_override_file_path_captured(self, tmp_path):
        path = _write(
            tmp_path,
            '[[harness]]\nname="claude_code"\n'
            '  [[harness.plugins]]\n  name="portage"\n'
            '    [[harness.plugins.subagents]]\n    name="updater"\n'
            '    file_path="/custom/updater.md"\n'
            '    [[harness.plugins.skills]]\n    name="pull_request"\n'
            '    file_path="/custom/SKILL.md"\n',
        )
        cfg = resolve_config(config_path=path, detected_harnesses=[])
        portage = cfg.harnesses[0].plugins[0]
        assert portage.subagents["updater"] == "/custom/updater.md"
        assert portage.skills["pull_request"] == "/custom/SKILL.md"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class TestValidation:
    def test_unknown_plugin_raises(self):
        with pytest.raises(ConfigResolveError, match="bogus"):
            resolve_config(cli_harnesses=["claude_code"], cli_plugins=["bogus"])

    def test_unknown_subagent_raises(self, tmp_path):
        path = _write(
            tmp_path,
            '[[harness]]\nname="claude_code"\n'
            '  [[harness.plugins]]\n  name="craft"\n  subagents=["nope"]\n',
        )
        with pytest.raises(UnknownSubagentError, match="nope"):
            resolve_config(config_path=path, detected_harnesses=[])

    def test_unknown_skill_raises(self, tmp_path):
        path = _write(
            tmp_path,
            '[[harness]]\nname="claude_code"\n'
            '  [[harness.plugins]]\n  name="craft"\n  skills=["nope"]\n',
        )
        with pytest.raises(UnknownSkillError, match="nope"):
            resolve_config(config_path=path, detected_harnesses=[])

    def test_missing_config_file_raises(self, tmp_path):
        with pytest.raises(ConfigResolveError, match="not found"):
            resolve_config(config_path=tmp_path / "ghost.toml", detected_harnesses=[])

    def test_malformed_toml_raises(self, tmp_path):
        path = _write(tmp_path, "this is ][ not toml\n")
        with pytest.raises(ConfigResolveError, match="malformed"):
            resolve_config(config_path=path, detected_harnesses=[])


# ---------------------------------------------------------------------------
# selection() bridge to wire
# ---------------------------------------------------------------------------


class TestSelectionBridge:
    def test_selection_shape_matches_wire(self):
        cfg = resolve_config(cli_harnesses=["claude_code"], cli_plugins=["camp"])
        sel = cfg.harnesses[0].selection()
        assert "camp" in sel
        subagents, skills = sel["camp"]
        assert skills == {}
        assert subagents == {}


# ---------------------------------------------------------------------------
# Real shipped default.toml
# ---------------------------------------------------------------------------


class TestShippedDefault:
    def test_default_toml_resolves_all_plugins(self):
        cfg = resolve_config(
            config_path=_REPO_ROOT / "config" / "default.toml",
            detected_harnesses=["claude_code"],
        )
        assert cfg.install_camp_cli is True
        assert cfg.install_lore_cli is True
        names = [p.name for p in cfg.harnesses[0].plugins]
        assert names == ["camp", "lore", "craft", "portage", "outpost"]
