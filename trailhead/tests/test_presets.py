"""Tests for trailhead/presets.py — PRESETS table + resolve().

TDD: these tests are written BEFORE the implementation. All must pass after
trailhead/presets.py is implemented.

The preset table (spec §832-838):
  minimal  = lore{capture, recall, sessions}
  standard = minimal + camp{} (base only, empty cap set) + forge{planning, execute, review, helpers}
  full     = every capability declared in each tool's capabilities.toml (computed at runtime)

The "full" preset is computed from load_manifest — it can never drift from the manifests.
"""

from pathlib import Path

import pytest

from trailhead.capabilities import load_manifest
from trailhead.presets import PresetError, resolve

_REPO_ROOT = Path(__file__).parent.parent.parent
_LORE_MANIFEST = _REPO_ROOT / "tools" / "lore" / "capabilities.toml"
_CAMP_MANIFEST = _REPO_ROOT / "tools" / "camp" / "capabilities.toml"
_FORGE_MANIFEST = _REPO_ROOT / "tools" / "forge" / "capabilities.toml"


# ---------------------------------------------------------------------------
# minimal preset
# ---------------------------------------------------------------------------


class TestMinimalPreset:
    def test_minimal_contains_lore(self):
        result = resolve("minimal")
        assert "lore" in result

    def test_minimal_lore_capabilities_exact(self):
        result = resolve("minimal")
        assert result["lore"] == {"capture", "recall", "sessions"}

    def test_minimal_has_no_camp(self):
        result = resolve("minimal")
        assert "camp" not in result

    def test_minimal_has_no_forge(self):
        result = resolve("minimal")
        assert "forge" not in result

    def test_minimal_has_exactly_one_tool(self):
        result = resolve("minimal")
        assert set(result.keys()) == {"lore"}


# ---------------------------------------------------------------------------
# standard preset
# ---------------------------------------------------------------------------


class TestStandardPreset:
    def test_standard_includes_lore(self):
        result = resolve("standard")
        assert "lore" in result

    def test_standard_lore_same_as_minimal(self):
        result = resolve("standard")
        assert result["lore"] == {"capture", "recall", "sessions"}

    def test_standard_includes_camp(self):
        result = resolve("standard")
        assert "camp" in result

    def test_standard_camp_base_only_empty_cap_set(self):
        """camp in standard has an empty capability set (base dirs only, no named caps)."""
        result = resolve("standard")
        assert result["camp"] == set()

    def test_standard_includes_forge(self):
        result = resolve("standard")
        assert "forge" in result

    def test_standard_forge_capabilities_exact(self):
        result = resolve("standard")
        assert result["forge"] == {"planning", "execute", "review", "helpers"}

    def test_standard_has_exactly_three_tools(self):
        result = resolve("standard")
        assert set(result.keys()) == {"lore", "camp", "forge"}

    def test_standard_forge_excludes_circle(self):
        result = resolve("standard")
        assert "circle" not in result["forge"]

    def test_standard_forge_excludes_design(self):
        result = resolve("standard")
        assert "design" not in result["forge"]

    def test_standard_forge_excludes_release(self):
        result = resolve("standard")
        assert "release" not in result["forge"]

    def test_standard_lore_excludes_shared_vaults(self):
        result = resolve("standard")
        assert "shared-vaults" not in result["lore"]


# ---------------------------------------------------------------------------
# full preset — computed from manifests (D-2)
# ---------------------------------------------------------------------------


class TestFullPreset:
    def test_full_contains_all_lore_capabilities(self):
        lore_manifest = load_manifest(_LORE_MANIFEST)
        result = resolve("full")
        assert result["lore"] == set(lore_manifest.capabilities.keys())

    def test_full_contains_all_camp_capabilities(self):
        camp_manifest = load_manifest(_CAMP_MANIFEST)
        result = resolve("full")
        assert result["camp"] == set(camp_manifest.capabilities.keys())

    def test_full_contains_all_forge_capabilities(self):
        forge_manifest = load_manifest(_FORGE_MANIFEST)
        result = resolve("full")
        assert result["forge"] == set(forge_manifest.capabilities.keys())

    def test_full_tracks_manifests(self):
        """full is the union of every capability declared in each tool's manifest."""
        lore_manifest = load_manifest(_LORE_MANIFEST)
        camp_manifest = load_manifest(_CAMP_MANIFEST)
        forge_manifest = load_manifest(_FORGE_MANIFEST)
        expected = {
            "lore": set(lore_manifest.capabilities.keys()),
            "camp": set(camp_manifest.capabilities.keys()),
            "forge": set(forge_manifest.capabilities.keys()),
        }
        result = resolve("full")
        assert result == expected

    def test_full_includes_shared_vaults(self):
        """shared-vaults is declared in lore manifest; full must include it."""
        result = resolve("full")
        assert "shared-vaults" in result["lore"]

    def test_full_includes_circle(self):
        result = resolve("full")
        assert "circle" in result["forge"]


# ---------------------------------------------------------------------------
# Unknown preset → named error listing valid presets
# ---------------------------------------------------------------------------


class TestUnknownPreset:
    def test_unknown_preset_raises_preset_error(self):
        with pytest.raises(PresetError):
            resolve("nonexistent")

    def test_unknown_preset_error_lists_valid_presets(self):
        with pytest.raises(PresetError) as exc_info:
            resolve("nonexistent")
        msg = str(exc_info.value)
        assert "minimal" in msg
        assert "standard" in msg
        assert "full" in msg

    def test_empty_string_preset_raises_preset_error(self):
        with pytest.raises(PresetError):
            resolve("")

    def test_preset_names_are_case_sensitive(self):
        with pytest.raises(PresetError):
            resolve("Standard")
