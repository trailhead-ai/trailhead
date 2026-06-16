"""
Tests for trailhead/paths.py — OS-aware config/state/cache-dir resolver.

TDD: these tests are written BEFORE the implementation. All must pass after
trailhead/paths.py is implemented.
"""

import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Import the module under test. We import lazily in some tests to avoid a
# circular concern, but the module-level import is the canonical way.
# ---------------------------------------------------------------------------
from trailhead.paths import (
    PathResolutionError,
    cache_dir,
    config_dir,
    ensure_dir,
    state_dir,
)


# ---------------------------------------------------------------------------
# U2 injection test — must be FIRST.
# Proves that the injection mechanism reaches all three OS branches on one dev box.
# ---------------------------------------------------------------------------


class TestU2InjectionReachesAllBranches:
    """The injection API must route to Linux, macOS, and Windows branches."""

    def test_linux_branch_reached_via_injection(self, tmp_path):
        env = {
            "HOME": str(tmp_path),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg_cfg"),
            "XDG_STATE_HOME": str(tmp_path / "xdg_state"),
            "XDG_CACHE_HOME": str(tmp_path / "xdg_cache"),
        }
        result = config_dir("myapp", platform="linux", env=env)
        assert result == tmp_path / "xdg_cfg" / "myapp"

    def test_macos_branch_reached_via_injection(self, tmp_path):
        env = {"HOME": str(tmp_path)}
        result = config_dir("myapp", platform="darwin", env=env)
        assert result == tmp_path / ".config" / "myapp"

    def test_windows_branch_reached_via_injection(self):
        env = {
            "APPDATA": r"C:\Users\foo\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\foo\AppData\Local",
        }
        result = config_dir("myapp", platform="win32", env=env)
        assert result == Path(r"C:\Users\foo\AppData\Roaming") / "myapp"

    def test_unknown_platform_raises_named_error(self):
        with pytest.raises(PathResolutionError, match="Unsupported platform"):
            config_dir("myapp", platform="os2warp", env={})


# ---------------------------------------------------------------------------
# Linux branch
# ---------------------------------------------------------------------------


class TestLinuxBranch:
    def test_config_dir_uses_xdg_config_home_when_set(self, tmp_path):
        xdg = tmp_path / "xdg_cfg"
        env = {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(xdg)}
        assert config_dir("app", platform="linux", env=env) == xdg / "app"

    def test_config_dir_falls_back_to_dotconfig(self, tmp_path):
        env = {"HOME": str(tmp_path)}
        assert config_dir("app", platform="linux", env=env) == tmp_path / ".config" / "app"

    def test_state_dir_uses_xdg_state_home_when_set(self, tmp_path):
        xdg = tmp_path / "xdg_state"
        env = {"HOME": str(tmp_path), "XDG_STATE_HOME": str(xdg)}
        assert state_dir("app", platform="linux", env=env) == xdg / "app"

    def test_state_dir_falls_back_to_local_state(self, tmp_path):
        env = {"HOME": str(tmp_path)}
        assert state_dir("app", platform="linux", env=env) == tmp_path / ".local" / "state" / "app"

    def test_cache_dir_uses_xdg_cache_home_when_set(self, tmp_path):
        xdg = tmp_path / "xdg_cache"
        env = {"HOME": str(tmp_path), "XDG_CACHE_HOME": str(xdg)}
        assert cache_dir("app", platform="linux", env=env) == xdg / "app"

    def test_cache_dir_falls_back_to_dotcache(self, tmp_path):
        env = {"HOME": str(tmp_path)}
        assert cache_dir("app", platform="linux", env=env) == tmp_path / ".cache" / "app"


# ---------------------------------------------------------------------------
# macOS branch
# ---------------------------------------------------------------------------


class TestMacosBranch:
    def test_config_dir_defaults_to_dotconfig(self, tmp_path):
        """basedir spec: macOS config defaults to ~/.config/<app>, mirroring Linux."""
        env = {"HOME": str(tmp_path)}
        result = config_dir("app", platform="darwin", env=env)
        assert result == tmp_path / ".config" / "app"

    def test_state_dir_defaults_to_local_state(self, tmp_path):
        """basedir spec: macOS state defaults to ~/.local/state/<app>, mirroring Linux."""
        env = {"HOME": str(tmp_path)}
        result = state_dir("app", platform="darwin", env=env)
        assert result == tmp_path / ".local" / "state" / "app"

    def test_cache_dir_defaults_to_dotcache(self, tmp_path):
        """basedir spec: macOS cache defaults to ~/.cache/<app>, mirroring Linux."""
        env = {"HOME": str(tmp_path)}
        result = cache_dir("app", platform="darwin", env=env)
        assert result == tmp_path / ".cache" / "app"

    def test_xdg_config_home_honored_on_macos_when_explicitly_set(self, tmp_path):
        xdg = tmp_path / "xdg_cfg"
        env = {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(xdg)}
        result = config_dir("app", platform="darwin", env=env)
        assert result == xdg / "app"

    def test_xdg_state_home_honored_on_macos_when_explicitly_set(self, tmp_path):
        xdg = tmp_path / "xdg_state"
        env = {"HOME": str(tmp_path), "XDG_STATE_HOME": str(xdg)}
        result = state_dir("app", platform="darwin", env=env)
        assert result == xdg / "app"

    def test_xdg_cache_home_honored_on_macos_when_explicitly_set(self, tmp_path):
        xdg = tmp_path / "xdg_cache"
        env = {"HOME": str(tmp_path), "XDG_CACHE_HOME": str(xdg)}
        result = cache_dir("app", platform="darwin", env=env)
        assert result == xdg / "app"


# ---------------------------------------------------------------------------
# macOS legacy-install migration fallback
#
# When trailhead flipped macOS to the basedir spec, existing installs already had
# data under ~/Library. The resolver falls back to the legacy path IFF the new XDG
# path is absent AND the legacy path exists — so we never orphan a live install,
# while new installs still land in the XDG location.
# ---------------------------------------------------------------------------


class TestMacosLegacyMigrationFallback:
    def test_config_falls_back_to_legacy_when_only_legacy_exists(self, tmp_path):
        legacy = tmp_path / "Library" / "Application Support" / "camp"
        legacy.mkdir(parents=True)
        env = {"HOME": str(tmp_path)}
        result = config_dir("camp", platform="darwin", env=env)
        assert result == legacy

    def test_state_falls_back_to_legacy_when_only_legacy_exists(self, tmp_path):
        legacy = tmp_path / "Library" / "Application Support" / "camp"
        legacy.mkdir(parents=True)
        env = {"HOME": str(tmp_path)}
        result = state_dir("camp", platform="darwin", env=env)
        assert result == legacy

    def test_cache_falls_back_to_legacy_caches_dir(self, tmp_path):
        legacy = tmp_path / "Library" / "Caches" / "camp"
        legacy.mkdir(parents=True)
        env = {"HOME": str(tmp_path)}
        result = cache_dir("camp", platform="darwin", env=env)
        assert result == legacy

    def test_new_xdg_path_wins_when_it_already_exists(self, tmp_path):
        """A migrated install (new path present) sticks to XDG even if legacy lingers."""
        new = tmp_path / ".config" / "camp"
        new.mkdir(parents=True)
        legacy = tmp_path / "Library" / "Application Support" / "camp"
        legacy.mkdir(parents=True)
        env = {"HOME": str(tmp_path)}
        result = config_dir("camp", platform="darwin", env=env)
        assert result == new

    def test_fresh_install_uses_xdg_when_neither_exists(self, tmp_path):
        env = {"HOME": str(tmp_path)}
        result = config_dir("camp", platform="darwin", env=env)
        assert result == tmp_path / ".config" / "camp"

    def test_xdg_override_skips_legacy_fallback(self, tmp_path):
        """An explicit XDG var opts out of the fallback even if legacy data exists."""
        legacy = tmp_path / "Library" / "Application Support" / "camp"
        legacy.mkdir(parents=True)
        xdg = tmp_path / "xdg_cfg"
        env = {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(xdg)}
        result = config_dir("camp", platform="darwin", env=env)
        assert result == xdg / "camp"

    def test_fallback_does_not_create_directories(self, tmp_path):
        """The existence check is read-only — resolving never materializes a dir."""
        env = {"HOME": str(tmp_path)}
        result = config_dir("camp", platform="darwin", env=env)
        assert not result.exists()


# ---------------------------------------------------------------------------
# Windows branch
# ---------------------------------------------------------------------------


class TestWindowsBranch:
    def test_config_dir_uses_appdata(self):
        env = {
            "APPDATA": r"C:\Users\foo\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\foo\AppData\Local",
        }
        result = config_dir("app", platform="win32", env=env)
        assert result == Path(r"C:\Users\foo\AppData\Roaming") / "app"

    def test_state_dir_uses_localappdata(self):
        env = {
            "APPDATA": r"C:\Users\foo\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\foo\AppData\Local",
        }
        result = state_dir("app", platform="win32", env=env)
        assert result == Path(r"C:\Users\foo\AppData\Local") / "app"

    def test_cache_dir_uses_localappdata(self):
        env = {
            "APPDATA": r"C:\Users\foo\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\foo\AppData\Local",
        }
        result = cache_dir("app", platform="win32", env=env)
        assert result == Path(r"C:\Users\foo\AppData\Local") / "app"

    def test_unset_appdata_raises_named_error(self):
        env = {"LOCALAPPDATA": r"C:\Users\foo\AppData\Local"}
        with pytest.raises(PathResolutionError, match="APPDATA"):
            config_dir("app", platform="win32", env=env)

    def test_unset_localappdata_raises_named_error_for_state(self):
        env = {"APPDATA": r"C:\Users\foo\AppData\Roaming"}
        with pytest.raises(PathResolutionError, match="LOCALAPPDATA"):
            state_dir("app", platform="win32", env=env)

    def test_unset_localappdata_raises_named_error_for_cache(self):
        env = {"APPDATA": r"C:\Users\foo\AppData\Roaming"}
        with pytest.raises(PathResolutionError, match="LOCALAPPDATA"):
            cache_dir("app", platform="win32", env=env)


# ---------------------------------------------------------------------------
# Per-app env override
# ---------------------------------------------------------------------------


class TestPerAppEnvOverride:
    def test_camp_state_dir_env_override_wins(self, tmp_path):
        override = tmp_path / "override_state"
        env = {
            "HOME": str(tmp_path),
            "CAMP_STATE_DIR": str(override),
        }
        result = state_dir("camp", platform="linux", env=env)
        assert result == override

    def test_camp_config_dir_env_override_wins(self, tmp_path):
        override = tmp_path / "override_config"
        env = {
            "HOME": str(tmp_path),
            "CAMP_CONFIG_DIR": str(override),
        }
        result = config_dir("camp", platform="linux", env=env)
        assert result == override

    def test_camp_cache_dir_env_override_wins(self, tmp_path):
        override = tmp_path / "override_cache"
        env = {
            "HOME": str(tmp_path),
            "CAMP_CACHE_DIR": str(override),
        }
        result = cache_dir("camp", platform="linux", env=env)
        assert result == override

    def test_per_app_override_does_not_affect_other_apps(self, tmp_path):
        env = {
            "HOME": str(tmp_path),
            "CAMP_STATE_DIR": str(tmp_path / "camp_override"),
        }
        # "other" app is not affected by CAMP_STATE_DIR
        result = state_dir("other", platform="linux", env=env)
        assert result == tmp_path / ".local" / "state" / "other"


# ---------------------------------------------------------------------------
# Unknown OS
# ---------------------------------------------------------------------------


class TestUnknownOS:
    def test_unknown_platform_config_raises_named_error(self):
        with pytest.raises(PathResolutionError, match="Unsupported platform"):
            config_dir("app", platform="haiku", env={})

    def test_unknown_platform_state_raises_named_error(self):
        with pytest.raises(PathResolutionError, match="Unsupported platform"):
            state_dir("app", platform="haiku", env={})

    def test_unknown_platform_cache_raises_named_error(self):
        with pytest.raises(PathResolutionError, match="Unsupported platform"):
            cache_dir("app", platform="haiku", env={})


# ---------------------------------------------------------------------------
# Unset HOME
# ---------------------------------------------------------------------------


class TestUnsetHome:
    def test_unset_home_on_linux_raises_named_error(self):
        """When HOME is unset, expanduser raises RuntimeError — must surface as PathResolutionError."""
        env = {}  # no HOME, no XDG
        with pytest.raises(PathResolutionError, match="HOME"):
            config_dir("app", platform="linux", env=env)

    def test_unset_home_on_macos_raises_named_error(self):
        env = {}
        with pytest.raises(PathResolutionError, match="HOME"):
            config_dir("app", platform="darwin", env=env)


# ---------------------------------------------------------------------------
# Empty and relative override validation
# ---------------------------------------------------------------------------


class TestOverrideValidation:
    def test_empty_xdg_config_home_is_ignored(self, tmp_path):
        """Empty XDG_CONFIG_HOME → fall through to default."""
        env = {"HOME": str(tmp_path), "XDG_CONFIG_HOME": ""}
        result = config_dir("app", platform="linux", env=env)
        assert result == tmp_path / ".config" / "app"

    def test_empty_xdg_state_home_is_ignored(self, tmp_path):
        env = {"HOME": str(tmp_path), "XDG_STATE_HOME": ""}
        result = state_dir("app", platform="linux", env=env)
        assert result == tmp_path / ".local" / "state" / "app"

    def test_empty_per_app_override_is_ignored(self, tmp_path):
        env = {"HOME": str(tmp_path), "CAMP_STATE_DIR": ""}
        result = state_dir("camp", platform="linux", env=env)
        assert result == tmp_path / ".local" / "state" / "camp"

    def test_relative_xdg_config_home_raises_named_error(self, tmp_path):
        """Relative XDG_CONFIG_HOME → PathResolutionError (not a silent cwd-relative path)."""
        env = {"HOME": str(tmp_path), "XDG_CONFIG_HOME": "relative/path"}
        with pytest.raises(PathResolutionError, match="relative"):
            config_dir("app", platform="linux", env=env)

    def test_relative_per_app_override_raises_named_error(self, tmp_path):
        env = {"HOME": str(tmp_path), "CAMP_STATE_DIR": "relative/state"}
        with pytest.raises(PathResolutionError, match="relative"):
            state_dir("camp", platform="linux", env=env)

    def test_relative_xdg_cache_home_raises_named_error(self, tmp_path):
        env = {"HOME": str(tmp_path), "XDG_CACHE_HOME": "relative/cache"}
        with pytest.raises(PathResolutionError, match="relative"):
            cache_dir("app", platform="linux", env=env)


# ---------------------------------------------------------------------------
# app arg path-separator / traversal validation
# ---------------------------------------------------------------------------


class TestAppArgValidation:
    def test_app_with_forward_slash_raises_named_error(self):
        with pytest.raises(PathResolutionError, match="app"):
            config_dir("bad/app", platform="linux", env={"HOME": "/tmp"})

    def test_app_with_backslash_raises_named_error(self):
        with pytest.raises(PathResolutionError, match="app"):
            config_dir("bad\\app", platform="linux", env={"HOME": "/tmp"})

    def test_app_with_dotdot_raises_named_error(self):
        with pytest.raises(PathResolutionError, match="app"):
            config_dir("../escape", platform="linux", env={"HOME": "/tmp"})

    def test_app_with_os_sep_raises_named_error(self):
        bad_app = "a" + os.sep + "b"
        with pytest.raises(PathResolutionError, match="app"):
            config_dir(bad_app, platform="linux", env={"HOME": "/tmp"})


# ---------------------------------------------------------------------------
# Purity: resolver never creates directories
# ---------------------------------------------------------------------------


class TestResolverPurity:
    def test_config_dir_does_not_create_directory(self, tmp_path):
        env = {"HOME": str(tmp_path)}
        result = config_dir("purity_test", platform="linux", env=env)
        assert not result.exists(), "config_dir must not create the directory"

    def test_state_dir_does_not_create_directory(self, tmp_path):
        env = {"HOME": str(tmp_path)}
        result = state_dir("purity_test", platform="linux", env=env)
        assert not result.exists()

    def test_cache_dir_does_not_create_directory(self, tmp_path):
        env = {"HOME": str(tmp_path)}
        result = cache_dir("purity_test", platform="linux", env=env)
        assert not result.exists()

    def test_resolver_returns_path_object(self, tmp_path):
        env = {"HOME": str(tmp_path)}
        result = config_dir("app", platform="linux", env=env)
        assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# ensure_dir
# ---------------------------------------------------------------------------


class TestEnsureDir:
    def test_ensure_dir_creates_directory(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "dir"
        ensure_dir(target)
        assert target.exists()
        assert target.is_dir()

    def test_ensure_dir_sets_mode_0o700(self, tmp_path):
        target = tmp_path / "secure_dir"
        ensure_dir(target)
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o700, f"Expected mode 0o700, got {oct(mode)}"

    def test_ensure_dir_returns_path(self, tmp_path):
        target = tmp_path / "returned_dir"
        result = ensure_dir(target)
        assert result == target
        assert isinstance(result, Path)

    def test_ensure_dir_is_idempotent(self, tmp_path):
        target = tmp_path / "idempotent"
        ensure_dir(target)
        ensure_dir(target)  # must not raise
        assert target.exists()

    def test_ensure_dir_respects_custom_mode(self, tmp_path):
        target = tmp_path / "custom_mode"
        ensure_dir(target, mode=0o750)
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o750, f"Expected mode 0o750, got {oct(mode)}"


# ---------------------------------------------------------------------------
# Camp consumer contract (D6)
# ---------------------------------------------------------------------------


class TestCampConsumerContract:
    def test_state_dir_camp_linux_returns_correct_path(self, tmp_path):
        """D6: state_dir('camp') on Linux → ~/.local/state/camp (expanded)."""
        env = {"HOME": str(tmp_path)}
        result = state_dir("camp", platform="linux", env=env)
        assert result == tmp_path / ".local" / "state" / "camp"

    def test_config_dir_camp_linux_returns_correct_path(self, tmp_path):
        """D6: config_dir('camp') on Linux → ~/.config/camp (expanded)."""
        env = {"HOME": str(tmp_path)}
        result = config_dir("camp", platform="linux", env=env)
        assert result == tmp_path / ".config" / "camp"

    def test_ensure_dir_creates_camp_state_dir(self, tmp_path):
        """D6: after ensure_dir(state_dir('camp')), the dir exists and is a Path."""
        env = {"HOME": str(tmp_path)}
        resolved = state_dir("camp", platform="linux", env=env)
        returned = ensure_dir(resolved)
        assert resolved.exists()
        assert isinstance(returned, Path)
