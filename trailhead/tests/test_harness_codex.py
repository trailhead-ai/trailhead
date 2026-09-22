"""Tests for trailhead/harness/codex.py — registry entry, home resolution, detection.

Every test injects its own env (``CODEX_HOME``/``HOME``/``PATH``) rather than
relying on the ambient process environment, per this suite's isolation
contract (see ``conftest.py``). ``_forbid_real_home`` poisons
``Path.home()`` for this whole suite, so any resolver reached here that fell
through to it would fail loudly rather than silently reading a real home.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from trailhead.harness import HarnessError, get_harness, known_harness_names
from trailhead.harness.codex import CodexHarness, codex_home


def _make_executable(path: Path) -> None:
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


class TestFactoryRegistration:
    def test_known_harness_names_includes_codex(self):
        assert "codex" in known_harness_names()

    def test_get_harness_returns_codex_instance(self):
        h = get_harness("codex")
        assert isinstance(h, CodexHarness)
        assert h.name == "codex"


class TestCodexHome:
    def test_prefers_codex_home_over_home(self, tmp_path):
        codex_dir = tmp_path / "explicit-codex-home"
        home_dir = tmp_path / "home"
        env = {"CODEX_HOME": str(codex_dir), "HOME": str(home_dir)}
        assert codex_home(env) == codex_dir

    def test_falls_back_to_home_dot_codex(self, tmp_path):
        home_dir = tmp_path / "home"
        env = {"HOME": str(home_dir)}
        assert codex_home(env) == home_dir / ".codex"

    def test_falls_back_to_userprofile_dot_codex(self, tmp_path):
        home_dir = tmp_path / "userprofile-home"
        env = {"USERPROFILE": str(home_dir)}
        assert codex_home(env) == home_dir / ".codex"

    def test_raises_on_relative_codex_home(self, tmp_path):
        env = {"CODEX_HOME": "relative/codex", "HOME": str(tmp_path / "home")}
        with pytest.raises(HarnessError):
            codex_home(env)

    def test_raises_when_neither_codex_home_nor_home_set(self):
        with pytest.raises(HarnessError):
            codex_home({})


class TestDetect:
    def test_true_with_codex_executable_on_path_and_no_home(self, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_executable(bin_dir / "codex")
        env = {"PATH": str(bin_dir)}
        assert CodexHarness.detect(env) is True

    def test_true_with_config_toml_under_codex_home_and_empty_path(self, tmp_path):
        codex_dir = tmp_path / "codex-home"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text("")
        env = {"CODEX_HOME": str(codex_dir), "PATH": ""}
        assert CodexHarness.detect(env) is True

    def test_false_with_bare_home_directory_and_empty_path(self, tmp_path):
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        env = {"HOME": str(home_dir), "PATH": ""}
        assert CodexHarness.detect(env) is False

    def test_false_when_path_entry_has_non_executable_file_named_codex(self, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "codex").write_text("not executable")
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        env = {"PATH": str(bin_dir), "HOME": str(home_dir)}
        assert CodexHarness.detect(env) is False


class TestVacuousInstallSurface:
    """The registration/install-state methods answer negatively regardless of
    on-disk state a Claude-shaped fixture would report positively for — the
    vacuous contract observed through the seam, not an absent method."""

    def test_reports_nothing_installed_against_a_claude_shaped_composed_root(self, tmp_path):
        composed_root = tmp_path / "composed" / "codex"
        composed_root.mkdir(parents=True)
        claude_dir = tmp_path / "claude-shaped-config-dir"
        claude_dir.mkdir()
        (claude_dir / ".trailhead-registered").write_text("{}")
        (claude_dir / ".trailhead-installed-lore").write_text("{}")
        (claude_dir / ".trailhead-installed-camp").write_text("{}")
        env = {"TRAILHEAD_CLAUDE_DIR": str(claude_dir), "HOME": str(tmp_path / "home")}

        harness = CodexHarness()

        assert harness.is_registered(composed_root, env=env) is False
        assert harness.is_installed("lore", composed_root, env=env) is False
        assert harness.installed_tools(composed_root, env=env) == []
