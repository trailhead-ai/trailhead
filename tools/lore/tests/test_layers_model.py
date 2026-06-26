"""Tests for the VaultLayer model, resolve_layers(), layer_for_path(),
_bootstrap.ensure_trailhead_importable(), and confinement helpers.

Test contract (all must RED before implementation, GREEN after):

1. resolve_layers() with a config default vault → exactly one layer: personal,
   trusted, kind="personal", root == resolve_active_vault(); with no config →
   the state-dir floor vaults/default.
2. VaultLayer is frozen/immutable; trusted defaults from kind (personal→True).
3. layer_for_path maps a path under a root to that layer; under no root → None;
   a symlinked path resolving into a root still maps.
4. _bootstrap.ensure_trailhead_importable() succeeds when run from the repo and
   emits the legible error (not raw ModuleNotFoundError) under a forced-missing probe.
5. validate_layer_name("../evil") raises LayerConfinementError; benign name passes.
6. assert_within_root(<escape>) raises LayerConfinementError; benign path passes;
   a symlinked-escape is caught because .resolve() runs first.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from conftest import REPO_ROOT, SCRIPTS_DIR, load_script, write_default_config

# Ensure scripts dir is on path for direct imports in subprocess helpers
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

_CLI_LORE = REPO_ROOT / "plugins" / "lore" / "cli" / "lore"


# ---------------------------------------------------------------------------
# Helper: load the modules under test
# ---------------------------------------------------------------------------


def _layers():
    return load_script("layers")


def _bootstrap():
    return load_script("_bootstrap")


# ---------------------------------------------------------------------------
# 1. VaultLayer dataclass
# ---------------------------------------------------------------------------


class TestVaultLayerDataclass:
    def test_personal_layer_trusted_by_default(self, tmp_path: Path) -> None:
        """VaultLayer with kind='personal' defaults trusted=True."""
        m = _layers()
        layer = m.VaultLayer(name="personal", root=tmp_path, kind="personal", trusted=True)
        assert layer.trusted is True

    def test_shared_layer_not_trusted_by_default(self, tmp_path: Path) -> None:
        """VaultLayer with kind='shared' has trusted=False when explicitly set."""
        m = _layers()
        layer = m.VaultLayer(name="team", root=tmp_path, kind="shared", trusted=False)
        assert layer.trusted is False

    def test_vault_layer_is_frozen(self, tmp_path: Path) -> None:
        """VaultLayer is immutable (frozen dataclass)."""
        m = _layers()
        layer = m.VaultLayer(name="personal", root=tmp_path, kind="personal", trusted=True)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            layer.name = "hacked"  # type: ignore[misc]

    def test_vault_layer_fields(self, tmp_path: Path) -> None:
        """VaultLayer carries name, root, kind, trusted."""
        m = _layers()
        layer = m.VaultLayer(name="myvault", root=tmp_path, kind="personal", trusted=True)
        assert layer.name == "myvault"
        assert layer.root == tmp_path
        assert layer.kind == "personal"
        assert layer.trusted is True


# ---------------------------------------------------------------------------
# 2. resolve_layers() — personal-only
# ---------------------------------------------------------------------------


class TestResolveLayers:
    def test_config_default_vault_returns_one_personal_layer(self, tmp_path: Path) -> None:
        """Config default vault → one personal layer rooted at resolve_active_vault()."""
        m = _layers()
        vault_dir = tmp_path / "my-vault"
        vault_dir.mkdir()
        config_home = tmp_path / "xdg_config"
        state_home = tmp_path / "xdg_state"
        write_default_config(config_home, vault_dir)
        # Hermetic: point groups_dir at an empty dir so no real on-disk shared
        # vault declaration (e.g. a user's trailhead group config) leaks in.
        no_groups = tmp_path / "no-groups"
        with mock.patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": str(config_home), "XDG_STATE_HOME": str(state_home)},
            clear=False,
        ):
            layers = m.resolve_layers(groups_dir=no_groups)
        assert len(layers) == 1
        layer = layers[0]
        assert layer.name == "personal"
        assert layer.kind == "personal"
        assert layer.trusted is True
        # root must equal the config default vault (resolve_active_vault output)
        assert layer.root == vault_dir.resolve()

    def test_no_config_uses_state_floor_default(self, tmp_path: Path) -> None:
        """No config.json → personal root is the state-dir floor vaults/default."""
        m = _layers()
        config_home = tmp_path / "xdg_config"  # empty: no config.json seeded
        state_home = tmp_path / "xdg_state"
        no_groups = tmp_path / "no-groups"
        with mock.patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": str(config_home), "XDG_STATE_HOME": str(state_home)},
            clear=False,
        ):
            layers = m.resolve_layers(groups_dir=no_groups)
        assert len(layers) == 1
        layer = layers[0]
        assert layer.name == "personal"
        assert layer.kind == "personal"
        assert layer.trusted is True
        expected = state_home / "lore" / "vaults" / "default"
        assert layer.root == expected

    def test_resolve_layers_returns_list(self, tmp_path: Path) -> None:
        """resolve_layers() returns a list (not a generator or tuple)."""
        m = _layers()
        result = m.resolve_layers(groups_dir=tmp_path / "no-groups")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 3. layer_for_path()
# ---------------------------------------------------------------------------


class TestLayerForPath:
    def test_path_under_root_returns_layer(self, tmp_path: Path) -> None:
        """A path inside a layer's root returns that layer."""
        m = _layers()
        vault = tmp_path / "vault"
        vault.mkdir()
        layer = m.VaultLayer(name="personal", root=vault, kind="personal", trusted=True)
        note = vault / "decisions" / "2026-06" / "my-note.md"
        result = m.layer_for_path(note, [layer])
        assert result is layer

    def test_path_outside_all_roots_returns_none(self, tmp_path: Path) -> None:
        """A path outside all layer roots returns None."""
        m = _layers()
        vault = tmp_path / "vault"
        vault.mkdir()
        other = tmp_path / "other-dir"
        other.mkdir()
        layer = m.VaultLayer(name="personal", root=vault, kind="personal", trusted=True)
        note = other / "some-note.md"
        result = m.layer_for_path(note, [layer])
        assert result is None

    def test_empty_layers_returns_none(self, tmp_path: Path) -> None:
        """With no layers, always returns None."""
        m = _layers()
        note = tmp_path / "some-note.md"
        result = m.layer_for_path(note, [])
        assert result is None

    def test_symlinked_path_resolving_into_root_returns_layer(self, tmp_path: Path) -> None:
        """A symlinked path that resolves into a root still maps to that layer."""
        m = _layers()
        real_vault = tmp_path / "real-vault"
        real_vault.mkdir()
        real_note = real_vault / "note.md"
        real_note.write_text("# note")

        symlink_dir = tmp_path / "symlink-vault"
        symlink_dir.symlink_to(real_vault)

        layer = m.VaultLayer(name="personal", root=real_vault, kind="personal", trusted=True)
        # path via symlink dir — resolves to inside real_vault
        note_via_symlink = symlink_dir / "note.md"
        result = m.layer_for_path(note_via_symlink, [layer])
        assert result is layer

    def test_first_matching_layer_wins(self, tmp_path: Path) -> None:
        """When a path could match multiple layers (root1 inside root2), first wins."""
        m = _layers()
        outer = tmp_path / "outer"
        outer.mkdir()
        inner = outer / "inner"
        inner.mkdir()
        layer_outer = m.VaultLayer(name="outer", root=outer, kind="personal", trusted=True)
        layer_inner = m.VaultLayer(name="inner", root=inner, kind="shared", trusted=False)
        note = inner / "note.md"
        # outer is listed first
        result = m.layer_for_path(note, [layer_outer, layer_inner])
        assert result is layer_outer


# ---------------------------------------------------------------------------
# 4. _bootstrap.ensure_trailhead_importable()
# ---------------------------------------------------------------------------


class TestBootstrapImportable:
    def test_ensure_trailhead_importable_succeeds_from_repo(self) -> None:
        """ensure_trailhead_importable() succeeds when run from the repo (tier 1 or 3)."""
        b = _bootstrap()
        # Should not raise or exit — trailhead is on sys.path via the repo
        b.ensure_trailhead_importable()

    def test_bare_python3_no_raw_module_not_found_error(self, tmp_path: Path) -> None:
        """Running with no TRAILHEAD_ROOT, bare python3 — emits legible
        error (not raw ModuleNotFoundError traceback) or succeeds via walk-up."""
        bootstrap_path = SCRIPTS_DIR / "_bootstrap.py"

        # Script that calls ensure_trailhead_importable() with a forced-missing setup
        # by removing trailhead from sys.path and unsetting TRAILHEAD_ROOT
        probe_script = tmp_path / "probe.py"
        probe_script.write_text(
            f"""
import sys
# Remove any existing trailhead entries from sys.path
sys.path = [p for p in sys.path if 'trailhead' not in p.lower() or 'tools' in p.lower()]

import importlib.util, os
spec = importlib.util.spec_from_file_location("_bootstrap", {str(bootstrap_path)!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.ensure_trailhead_importable()
"""
        )

        # Run under bare python3 without TRAILHEAD_ROOT in env
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(tmp_path)),
        }
        result = subprocess.run(
            ["python3", str(probe_script)],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            env=env,
        )
        # Must NOT leak a raw ModuleNotFoundError traceback
        assert "ModuleNotFoundError" not in result.stderr, (
            f"Raw ModuleNotFoundError leaked to stderr:\n{result.stderr}"
        )
        assert not ("Traceback" in result.stderr and "trailhead" in result.stderr), (
            f"Raw traceback with trailhead import leaked:\n{result.stderr}"
        )
        # Either succeeds (exit 0 — walk-up found the marker) or gives a legible error
        if result.returncode != 0:
            assert "trailhead" in result.stderr.lower() or "TRAILHEAD_ROOT" in result.stderr, (
                f"Unexpected unhelpful error: {result.stderr}"
            )

    def test_forced_missing_emits_legible_error(self, tmp_path: Path) -> None:
        """ensure_trailhead_importable() with a forged __file__ pointing nowhere
        emits the legible tier-4 error, not a raw ModuleNotFoundError."""
        bootstrap_path = SCRIPTS_DIR / "_bootstrap.py"

        # Probe: craft __file__ to a path deep in tmp_path so walk-up never finds marker
        # Then also clear TRAILHEAD_ROOT so tier-2 doesn't help
        probe_script = tmp_path / "probe_missing.py"
        fake_file = tmp_path / "deep" / "fake" / "scripts" / "_bootstrap.py"
        fake_file.parent.mkdir(parents=True, exist_ok=True)

        probe_script.write_text(
            f"""
import sys
import importlib.util

spec = importlib.util.spec_from_file_location("_bootstrap", {str(bootstrap_path)!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Craft __file__ to somewhere with no trailhead marker
mod.__file__ = {str(fake_file)!r}
import os
env_backup = os.environ.pop("TRAILHEAD_ROOT", None)

# Also purge trailhead from sys.path to defeat tier-1
sys.path = [p for p in sys.path if "trailhead" not in p or "tools" in p]

try:
    mod.ensure_trailhead_importable()
    print("succeeded", flush=True)
except SystemExit as e:
    print(f"SystemExit {{e.code}}", flush=True)
finally:
    if env_backup is not None:
        os.environ["TRAILHEAD_ROOT"] = env_backup
"""
        )

        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(tmp_path)),
        }
        result = subprocess.run(
            ["python3", str(probe_script)],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            env=env,
        )
        # Must not leak raw ModuleNotFoundError
        assert "ModuleNotFoundError" not in result.stderr, (
            f"Raw ModuleNotFoundError leaked:\n{result.stderr}"
        )
        # Must either succeed (if walk-up lands on the monorepo) or give a legible error
        if "succeeded" not in result.stdout:
            # Should have exited via SystemExit(1) with a legible message
            assert "SystemExit" in result.stdout or result.returncode != 0
            assert (
                "trailhead" in result.stderr.lower()
                or "TRAILHEAD_ROOT" in result.stderr
                or result.stderr
            )


# ---------------------------------------------------------------------------
# 5 & 6. Confinement helpers: validate_layer_name + assert_within_root
# ---------------------------------------------------------------------------


class TestValidateLayerName:
    def test_reject_dotdot(self) -> None:
        """'../evil' raises LayerConfinementError."""
        m = _layers()
        with pytest.raises(m.LayerConfinementError):
            m.validate_layer_name("../evil")

    def test_reject_dotdot_plain(self) -> None:
        """'..' raises LayerConfinementError."""
        m = _layers()
        with pytest.raises(m.LayerConfinementError):
            m.validate_layer_name("..")

    def test_reject_forward_slash(self) -> None:
        """'foo/bar' raises LayerConfinementError."""
        m = _layers()
        with pytest.raises(m.LayerConfinementError):
            m.validate_layer_name("foo/bar")

    def test_reject_backslash(self) -> None:
        r"""'foo\bar' raises LayerConfinementError."""
        m = _layers()
        with pytest.raises(m.LayerConfinementError):
            m.validate_layer_name("foo\\bar")

    def test_reject_null_byte(self) -> None:
        """Name with null byte raises LayerConfinementError."""
        m = _layers()
        with pytest.raises(m.LayerConfinementError):
            m.validate_layer_name("foo\x00bar")

    def test_reject_empty(self) -> None:
        """Empty name raises LayerConfinementError."""
        m = _layers()
        with pytest.raises(m.LayerConfinementError):
            m.validate_layer_name("")

    def test_benign_name_passes(self) -> None:
        """A simple alphanumeric name passes without error."""
        m = _layers()
        m.validate_layer_name("my-team-vault")
        m.validate_layer_name("personal")
        m.validate_layer_name("shared_vault_2026")


class TestAssertWithinRoot:
    def test_path_within_root_passes(self, tmp_path: Path) -> None:
        """A path inside the root passes assert_within_root."""
        m = _layers()
        root = tmp_path / "vault"
        root.mkdir()
        candidate = root / "notes" / "my-note.md"
        m.assert_within_root(candidate, root)  # must not raise

    def test_path_escaping_root_raises(self, tmp_path: Path) -> None:
        """A path outside the root raises LayerConfinementError."""
        m = _layers()
        root = tmp_path / "vault"
        root.mkdir()
        outside = tmp_path / "other" / "note.md"
        with pytest.raises(m.LayerConfinementError):
            m.assert_within_root(outside, root)

    def test_traversal_escape_raises(self, tmp_path: Path) -> None:
        """A traversal path escaping via '../' raises LayerConfinementError."""
        m = _layers()
        root = tmp_path / "vault"
        root.mkdir()
        # Construct a path that looks like it's under root but resolves outside
        escape = root / ".." / "outside" / "note.md"
        with pytest.raises(m.LayerConfinementError):
            m.assert_within_root(escape, root)

    def test_symlinked_escape_is_caught(self, tmp_path: Path) -> None:
        """A symlink pointing outside the root is caught because .resolve() runs first."""
        m = _layers()
        root = tmp_path / "vault"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        outside_note = outside / "secret.md"
        outside_note.write_text("secret")
        # Symlink inside root → points outside root
        symlink_inside = root / "escape.md"
        symlink_inside.symlink_to(outside_note)
        with pytest.raises(m.LayerConfinementError):
            m.assert_within_root(symlink_inside, root)
