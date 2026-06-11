"""Tests for trailhead/wire.py — multi-tool orchestrator (Slice 3).

TDD: written BEFORE implementation. All must fail first, then pass.

Contract:
  - wire(selection, *, env) composes and registers each tool in selection.
  - A tool with zero selected capabilities is skipped (no dir created).
  - Composed tree lands at <mkt_root>/plugins/<tool>/ via staging + atomic promote.
  - marketplace.json is generated at <mkt_root>/.claude-plugin/.
  - The harness CLI runner is stubbed in all tests (B-3 hermeticity).
  - S-2: apply_plan(mode="copy") — no symlinks in composed tree.
  - R-1: staging-dir + atomic promote — a mid-compose failure leaves the
    prior dest unchanged, not half-written.
  - B-5: minimal preset → no camp/forge dests.
  - Re-wiring same selection is idempotent.
  - structural validity: dest/.claude-plugin/plugin.json parses.
"""

import json
import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest


_REPO_ROOT = Path(__file__).parent.parent.parent
_LORE_MANIFEST = _REPO_ROOT / "tools" / "lore" / "capabilities.toml"
_CAMP_MANIFEST = _REPO_ROOT / "tools" / "camp" / "capabilities.toml"
_FORGE_MANIFEST = _REPO_ROOT / "tools" / "forge" / "capabilities.toml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _noop_runner(args, **kwargs):
    """Stub harness-CLI runner that does nothing."""
    pass


def _env(tmp_path: Path) -> dict[str, str]:
    """Return an env dict that redirects TRAILHEAD_STATE_DIR to tmp_path."""
    return {**os.environ, "TRAILHEAD_STATE_DIR": str(tmp_path)}


def _manifest_paths() -> dict[str, Path]:
    return {
        "lore": _LORE_MANIFEST,
        "camp": _CAMP_MANIFEST,
        "forge": _FORGE_MANIFEST,
    }


# ---------------------------------------------------------------------------
# T-W1: minimal preset — lore only, no camp/forge dests (B-5 enforcement)
# ---------------------------------------------------------------------------


class TestMinimalPresetGating:
    def test_minimal_lore_dest_exists(self, tmp_path):
        """wire with minimal preset creates lore dest."""
        from trailhead.wire import wire

        selection = {"lore": {"capture", "recall", "sessions"}}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        mkt_root = tmp_path / "composed" / "lore"
        plugin_dest = mkt_root / "plugins" / "lore"
        assert plugin_dest.exists(), "lore plugin dest missing after minimal wire"

    def test_minimal_no_camp_dest(self, tmp_path):
        """wire with minimal preset creates NO camp dest (B-5)."""
        from trailhead.wire import wire

        selection = {"lore": {"capture", "recall", "sessions"}}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        camp_dest = tmp_path / "composed" / "camp" / "plugins" / "camp"
        assert not camp_dest.exists(), (
            "camp dest exists despite minimal preset (B-5 violation)"
        )

    def test_minimal_no_forge_dest(self, tmp_path):
        """wire with minimal preset creates NO forge dest (B-5)."""
        from trailhead.wire import wire

        selection = {"lore": {"capture", "recall", "sessions"}}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        forge_dest = tmp_path / "composed" / "forge" / "plugins" / "forge"
        assert not forge_dest.exists(), (
            "forge dest exists despite minimal preset (B-5 violation)"
        )

    def test_empty_selection_for_tool_skips_it(self, tmp_path):
        """A tool with an empty capability set but present in selection is still wired
        (base-only), but a tool absent from selection is NOT created."""
        from trailhead.wire import wire

        # Provide lore only — camp and forge are not in selection
        selection = {"lore": {"capture"}}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        assert not (tmp_path / "composed" / "camp").exists()
        assert not (tmp_path / "composed" / "forge").exists()


# ---------------------------------------------------------------------------
# T-W2: standard preset — lore + camp + forge dests; circle/design/release absent
# ---------------------------------------------------------------------------


class TestStandardPreset:
    def test_standard_lore_dest_exists(self, tmp_path):
        from trailhead.wire import wire

        selection = {
            "lore": {"capture", "recall", "sessions"},
            "camp": set(),
            "forge": {"planning", "execute", "review", "helpers"},
        }
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        assert (tmp_path / "composed" / "lore" / "plugins" / "lore").exists()

    def test_standard_camp_dest_exists(self, tmp_path):
        from trailhead.wire import wire

        selection = {
            "lore": {"capture", "recall", "sessions"},
            "camp": set(),
            "forge": {"planning", "execute", "review", "helpers"},
        }
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        assert (tmp_path / "composed" / "camp" / "plugins" / "camp").exists()

    def test_standard_forge_dest_exists(self, tmp_path):
        from trailhead.wire import wire

        selection = {
            "lore": {"capture", "recall", "sessions"},
            "camp": set(),
            "forge": {"planning", "execute", "review", "helpers"},
        }
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        assert (tmp_path / "composed" / "forge" / "plugins" / "forge").exists()

    def test_standard_forge_circle_skills_absent(self, tmp_path):
        """circle capability skills absent when forge wired without circle."""
        from trailhead.capabilities import load_manifest
        from trailhead.wire import wire

        selection = {
            "lore": {"capture", "recall", "sessions"},
            "camp": set(),
            "forge": {"planning", "execute", "review", "helpers"},
        }
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        forge_manifest = load_manifest(_FORGE_MANIFEST)
        forge_dest = tmp_path / "composed" / "forge" / "plugins" / "forge"
        # circle has skills=[] anyway, but agents must not leak
        for agent in forge_manifest.capabilities["circle"]["agents"]:
            assert not (forge_dest / agent).exists(), (
                f"circle agent {agent!r} present in forge dest despite circle not selected"
            )

    def test_standard_forge_design_release_absent(self, tmp_path):
        """design and release skill dirs absent when not in standard preset."""
        from trailhead.capabilities import load_manifest
        from trailhead.wire import wire

        selection = {
            "lore": {"capture", "recall", "sessions"},
            "camp": set(),
            "forge": {"planning", "execute", "review", "helpers"},
        }
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        forge_manifest = load_manifest(_FORGE_MANIFEST)
        forge_dest = tmp_path / "composed" / "forge" / "plugins" / "forge"
        for absent_cap in ("design", "release"):
            for skill in forge_manifest.capabilities[absent_cap]["skills"]:
                assert not (forge_dest / skill).exists(), (
                    f"{absent_cap} skill {skill!r} present in standard forge dest"
                )

    def test_lore_shared_vaults_absent_in_standard(self, tmp_path):
        """lore shared-vaults absent in standard (not in lore standard caps)."""
        from trailhead.capabilities import load_manifest
        from trailhead.wire import wire

        selection = {
            "lore": {"capture", "recall", "sessions"},
            "camp": set(),
            "forge": {"planning", "execute", "review", "helpers"},
        }
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        lore_manifest = load_manifest(_LORE_MANIFEST)
        lore_dest = tmp_path / "composed" / "lore" / "plugins" / "lore"
        for skill in lore_manifest.capabilities["shared-vaults"]["skills"]:
            assert not (lore_dest / skill).exists()


# ---------------------------------------------------------------------------
# T-W3: structural validity — plugin.json parses; marketplace.json references tool
# ---------------------------------------------------------------------------


class TestStructuralValidity:
    def test_plugin_json_exists_and_parses(self, tmp_path):
        """Composed lore dest has a valid .claude-plugin/plugin.json."""
        from trailhead.wire import wire

        selection = {"lore": {"capture"}}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        plugin_dest = tmp_path / "composed" / "lore" / "plugins" / "lore"
        plugin_json = plugin_dest / ".claude-plugin" / "plugin.json"
        assert plugin_json.exists()
        data = json.loads(plugin_json.read_text())
        assert isinstance(data, dict)

    def test_marketplace_json_exists_and_references_tool(self, tmp_path):
        """marketplace.json at mkt_root/.claude-plugin/ names the wired tool."""
        from trailhead.wire import wire

        selection = {"lore": {"capture"}}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        mkt_json = tmp_path / "composed" / "lore" / ".claude-plugin" / "marketplace.json"
        assert mkt_json.exists()
        data = json.loads(mkt_json.read_text())
        assert data["name"] == "trailhead-lore"
        assert any(p["name"] == "lore" for p in data["plugins"])

    def test_s2_no_symlinks_in_wired_tree(self, tmp_path):
        """S-2: the composed tree must contain no symlinks."""
        from trailhead.wire import wire

        selection = {"lore": {"capture", "recall"}, "forge": {"planning", "helpers"}}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        composed_root = tmp_path / "composed"
        for path in composed_root.rglob("*"):
            assert not path.is_symlink(), (
                f"symlink found in composed tree (S-2 violation): {path}"
            )


# ---------------------------------------------------------------------------
# T-W4: idempotency — re-wiring same selection leaves same tree, no duplicates
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_rewire_same_selection_idempotent(self, tmp_path):
        """Calling wire twice with the same selection produces the same tree."""
        from trailhead.wire import wire

        selection = {"lore": {"capture", "recall"}}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        # Capture tree state
        plugin_dest = tmp_path / "composed" / "lore" / "plugins" / "lore"
        first_files = {
            str(p.relative_to(plugin_dest))
            for p in plugin_dest.rglob("*")
            if p.is_file()
        }

        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        second_files = {
            str(p.relative_to(plugin_dest))
            for p in plugin_dest.rglob("*")
            if p.is_file()
        }
        assert first_files == second_files, (
            "re-wiring same selection changed the file set"
        )

    def test_rewire_removes_previously_present_capability(self, tmp_path):
        """Re-wiring a narrower selection removes the previously-wired caps."""
        from trailhead.wire import wire

        # First wire: lore with recall (has lore-librarian agent)
        selection_full = {"lore": {"capture", "recall", "sessions"}}
        wire(
            selection_full,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        plugin_dest = tmp_path / "composed" / "lore" / "plugins" / "lore"
        assert (plugin_dest / "agents" / "lore-librarian.md").exists()

        # Re-wire: lore with capture only (no recall → no lore-librarian agent)
        selection_narrow = {"lore": {"capture"}}
        wire(
            selection_narrow,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        # recall skills/agents must be gone
        assert not (plugin_dest / "agents" / "lore-librarian.md").exists(), (
            "lore-librarian.md still present after rewiring without recall (S-4/R-1)"
        )
        assert not (plugin_dest / "skills" / "review").exists(), (
            "skills/review still present after rewiring without recall"
        )


# ---------------------------------------------------------------------------
# T-W5: R-1 atomicity — mid-compose failure leaves prior dest unchanged
# ---------------------------------------------------------------------------


class TestAtomicPromote:
    def test_mid_compose_failure_leaves_prior_dest_intact(self, tmp_path):
        """R-1: if compose fails mid-way, the prior wired dest is untouched."""
        from trailhead.wire import wire

        # First wire: establish a known-good dest
        selection = {"lore": {"capture"}}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        plugin_dest = tmp_path / "composed" / "lore" / "plugins" / "lore"
        assert plugin_dest.exists()

        # Capture the set of files in the live dest
        before_files = {
            str(p.relative_to(plugin_dest))
            for p in plugin_dest.rglob("*")
            if p.is_file()
        }

        # Patch shutil.copy2 to raise on the first call (simulates mid-copy failure)
        original_copy2 = shutil.copy2
        call_count = {"n": 0}

        def failing_copy2(src, dst, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("simulated disk-full mid-compose")
            return original_copy2(src, dst, **kwargs)

        with patch("shutil.copy2", side_effect=failing_copy2):
            with pytest.raises(OSError, match="simulated disk-full"):
                wire(
                    selection,
                    manifest_paths=_manifest_paths(),
                    env=_env(tmp_path),
                    runner=_noop_runner,
                )

        # The live dest must be unchanged
        after_files = {
            str(p.relative_to(plugin_dest))
            for p in plugin_dest.rglob("*")
            if p.is_file()
        }
        assert before_files == after_files, (
            "R-1 violated: mid-compose failure mutated the live dest\n"
            f"  removed: {before_files - after_files}\n"
            f"  added:   {after_files - before_files}"
        )

    def test_first_wire_failure_leaves_no_dest(self, tmp_path):
        """R-1: if the very first wire fails, no partial dest is left."""
        from trailhead.wire import wire

        selection = {"lore": {"capture"}}
        plugin_dest = tmp_path / "composed" / "lore" / "plugins" / "lore"
        assert not plugin_dest.exists()

        original_copy2 = shutil.copy2
        call_count = {"n": 0}

        def failing_copy2(src, dst, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("simulated failure on first wire")
            return original_copy2(src, dst, **kwargs)

        with patch("shutil.copy2", side_effect=failing_copy2):
            with pytest.raises(OSError):
                wire(
                    selection,
                    manifest_paths=_manifest_paths(),
                    env=_env(tmp_path),
                    runner=_noop_runner,
                )

        # No partial dest created
        assert not plugin_dest.exists(), (
            "R-1 violated: partial dest exists after failed first wire"
        )


# ---------------------------------------------------------------------------
# T-W6: registry runner called with expected args
# ---------------------------------------------------------------------------


class TestRegistryRunnerArgs:
    def test_register_called_for_each_wired_tool(self, tmp_path):
        """register() is called once per wired tool."""
        from trailhead.wire import wire

        selection = {"lore": {"capture"}, "forge": {"planning"}}
        calls = []

        def stub_runner(args, **kwargs):
            calls.append(list(args))

        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=stub_runner,
        )
        # Each tool should produce a 'marketplace add' + 'install' call
        marketplace_adds = [c for c in calls if "marketplace" in c and "add" in c]
        installs = [c for c in calls if "install" in c]
        assert len(marketplace_adds) == 2, (
            f"expected 2 marketplace add calls (lore+forge), got {marketplace_adds}"
        )
        assert len(installs) == 2, (
            f"expected 2 install calls (lore+forge), got {installs}"
        )

    def test_runner_never_invokes_real_subprocess(self, tmp_path):
        """wire with a stub runner must not touch subprocess.run."""
        from trailhead.wire import wire

        selection = {"lore": {"capture"}}
        with patch("subprocess.run") as mock_run:
            wire(
                selection,
                manifest_paths=_manifest_paths(),
                env=_env(tmp_path),
                runner=_noop_runner,
            )
            mock_run.assert_not_called()

    def test_mkt_root_passed_to_marketplace_add(self, tmp_path):
        """marketplace add call includes the correct mkt_root path."""
        from trailhead.wire import wire

        selection = {"lore": {"capture"}}
        calls = []

        def stub_runner(args, **kwargs):
            calls.append(list(args))

        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=stub_runner,
        )
        add_calls = [c for c in calls if "marketplace" in c and "add" in c]
        assert len(add_calls) == 1
        # mkt_root should be tmp_path/composed/lore
        expected_mkt_root = str(tmp_path / "composed" / "lore")
        assert expected_mkt_root in add_calls[0], (
            f"expected mkt_root {expected_mkt_root!r} in add call: {add_calls[0]}"
        )


# ---------------------------------------------------------------------------
# T-W7: lore minimal wire content check (canonical integration)
# ---------------------------------------------------------------------------


class TestMinimalLoreContent:
    def test_lore_minimal_skills_present(self, tmp_path):
        """Minimal lore selection: capture/recall/sessions skills are in dest."""
        from trailhead.capabilities import load_manifest
        from trailhead.wire import wire

        selection = {"lore": {"capture", "recall", "sessions"}}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        lore_dest = tmp_path / "composed" / "lore" / "plugins" / "lore"
        lore_manifest = load_manifest(_LORE_MANIFEST)
        for cap in ("capture", "recall", "sessions"):
            for skill in lore_manifest.capabilities[cap]["skills"]:
                assert (lore_dest / skill).exists(), (
                    f"skill {skill!r} missing from minimal lore dest"
                )

    def test_lore_minimal_lore_librarian_agent_present(self, tmp_path):
        """Minimal lore includes recall → lore-librarian.md agent in dest."""
        from trailhead.wire import wire

        selection = {"lore": {"capture", "recall", "sessions"}}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        lore_dest = tmp_path / "composed" / "lore" / "plugins" / "lore"
        assert (lore_dest / "agents" / "lore-librarian.md").exists(), (
            "lore-librarian.md missing from minimal lore dest"
        )
