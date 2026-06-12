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
  - C-1.1: staging cleanup runs on ANY exception (try/finally), including
    BaseException subclasses like KeyboardInterrupt.
  - C-1.2/I-1: WireError names tool + stage on per-tool failure; multi-tool
    wire is best-effort sequential (already-processed tools stay committed).
  - B-5: minimal preset → no camp/forge dests.
  - Re-wiring same selection is idempotent.
  - structural validity: dest/.claude-plugin/plugin.json parses.
  - Minor-2: cross-tool dest collision is structurally unreachable (each tool
    composes into its own composed/<tool>/plugins/<tool> namespace).
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
# T-W2: standard preset — lore + camp + forge dests; council/design/release absent
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

    def test_standard_forge_council_agents_absent(self, tmp_path):
        """council capability agents absent when forge wired without council.

        M-5 fix: council has skills=[], so testing skill absence is vacuous.
        council DOES have 4 agents (advocate/builder/breaker/attacker) — assert none appear in dest.
        """
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
        council_agents = forge_manifest.capabilities["council"]["agents"]
        # Confirm we're testing something real
        assert len(council_agents) > 0, "test is vacuous: council has no agents"
        for agent in council_agents:
            assert not (forge_dest / agent).exists(), (
                f"council agent {agent!r} present in forge dest despite council not selected"
            )

    def test_standard_forge_unselected_council_and_execute_absent(self, tmp_path):
        """Agents from unselected council capability must not appear in the wired dest.

        M-5 fix: design and release both have skills=[] and agents=[], making their
        absence loops vacuous.  council has 4 real agents (advocate/builder/breaker/attacker) that are
        structurally excluded when council is not in the selection.  execute also has
        real agents (assumption-prover/executor) — kept here for symmetry.
        """
        from trailhead.capabilities import load_manifest
        from trailhead.wire import wire

        # Wire standard forge WITHOUT council or execute
        selection = {
            "lore": {"capture", "recall", "sessions"},
            "camp": set(),
            "forge": {"planning", "review", "helpers"},
        }
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        forge_manifest = load_manifest(_FORGE_MANIFEST)
        forge_dest = tmp_path / "composed" / "forge" / "plugins" / "forge"
        for absent_cap in ("council", "execute"):
            agents = forge_manifest.capabilities[absent_cap]["agents"]
            assert len(agents) > 0, f"test is vacuous: {absent_cap} has no agents"
            for agent in agents:
                assert not (forge_dest / agent).exists(), (
                    f"{absent_cap} agent {agent!r} present in forge dest despite "
                    f"{absent_cap} not being selected"
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

        # First wire: lore with recall (has librarian agent)
        selection_full = {"lore": {"capture", "recall", "sessions"}}
        wire(
            selection_full,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        plugin_dest = tmp_path / "composed" / "lore" / "plugins" / "lore"
        assert (plugin_dest / "agents" / "librarian.md").exists()

        # Re-wire: lore with capture only (no recall → no librarian agent)
        selection_narrow = {"lore": {"capture"}}
        wire(
            selection_narrow,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        # recall's agent must be gone (recall is now a librarian-only capability;
        # its skills/tend + skills/reflect were deleted in Slice 7).
        assert not (plugin_dest / "agents" / "librarian.md").exists(), (
            "librarian.md still present after rewiring without recall (S-4/R-1)"
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
            with pytest.raises(Exception):  # WireError wrapping OSError
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
            with pytest.raises(Exception):  # WireError wrapping OSError
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
        """Minimal lore includes recall → librarian.md agent in dest."""
        from trailhead.wire import wire

        selection = {"lore": {"capture", "recall", "sessions"}}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        lore_dest = tmp_path / "composed" / "lore" / "plugins" / "lore"
        assert (lore_dest / "agents" / "librarian.md").exists(), (
            "librarian.md missing from minimal lore dest"
        )


# ---------------------------------------------------------------------------
# T-W8: C-1.1 — staging cleanup runs on BaseException (try/finally), not
#        just Exception, so KeyboardInterrupt orphans no staging dir.
# ---------------------------------------------------------------------------


class TestStagingCleanupOnBaseException:
    def test_staging_dir_cleaned_on_keyboard_interrupt(self, tmp_path):
        """C-1.1: a KeyboardInterrupt mid-compose must leave no staging dir."""
        import trailhead.wire as wire_mod
        from trailhead.wire import wire

        staging_parent = tmp_path / "composed" / "lore" / "plugins"

        def raising_compose_plan(manifest, caps, dest):
            # Let staging dir creation happen inside _compose_tool first;
            # raise KeyboardInterrupt to simulate interrupt mid-compose.
            raise KeyboardInterrupt("simulated interrupt")

        with patch.object(wire_mod, "compose_plan", side_effect=raising_compose_plan):
            with pytest.raises((KeyboardInterrupt, Exception)):
                wire(
                    {"lore": {"capture"}},
                    manifest_paths=_manifest_paths(),
                    env=_env(tmp_path),
                    runner=_noop_runner,
                )

        # No _<tool>_staging_* dir should survive
        if staging_parent.exists():
            leftover = list(staging_parent.glob("_lore_staging_*"))
            assert leftover == [], (
                f"C-1.1 violated: staging dirs orphaned after KeyboardInterrupt: {leftover}"
            )

    def test_staging_dir_cleaned_on_system_exit(self, tmp_path):
        """C-1.1: a SystemExit mid-compose must leave no staging dir."""
        import trailhead.wire as wire_mod
        from trailhead.wire import wire

        staging_parent = tmp_path / "composed" / "lore" / "plugins"

        def raising_compose_plan(manifest, caps, dest):
            raise SystemExit(1)

        with patch.object(wire_mod, "compose_plan", side_effect=raising_compose_plan):
            with pytest.raises((SystemExit, Exception)):
                wire(
                    {"lore": {"capture"}},
                    manifest_paths=_manifest_paths(),
                    env=_env(tmp_path),
                    runner=_noop_runner,
                )

        if staging_parent.exists():
            leftover = list(staging_parent.glob("_lore_staging_*"))
            assert leftover == [], (
                f"C-1.1 violated: staging dirs orphaned after SystemExit: {leftover}"
            )

    def test_successful_promote_leaves_no_staging_dir(self, tmp_path):
        """After a successful wire, no staging dir lingers under plugins/."""
        from trailhead.wire import wire

        wire(
            {"lore": {"capture"}},
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        staging_parent = tmp_path / "composed" / "lore" / "plugins"
        leftover = list(staging_parent.glob("_lore_staging_*"))
        assert leftover == [], (
            f"staging dir not cleaned after successful wire: {leftover}"
        )


# ---------------------------------------------------------------------------
# T-W9: C-1.2/I-1 — WireError names the failing tool + stage; multi-tool
#        best-effort sequential semantics (already-processed tools stay wired).
# ---------------------------------------------------------------------------


class TestWireErrorIsolation:
    def test_wire_error_raised_naming_failing_tool(self, tmp_path):
        """C-1.2: a per-tool failure raises WireError naming the tool."""
        import trailhead.wire as wire_mod
        from trailhead.wire import WireError, wire

        call_count = {"n": 0}
        original_compose_plan = wire_mod.compose_plan

        def forge_failing_plan(manifest, caps, dest):
            call_count["n"] += 1
            if manifest.tool_name == "forge":
                raise RuntimeError("forge compose exploded")
            return original_compose_plan(manifest, caps, dest)

        with patch.object(wire_mod, "compose_plan", side_effect=forge_failing_plan):
            with pytest.raises(WireError) as exc_info:
                wire(
                    {"lore": {"capture"}, "forge": {"planning"}},
                    manifest_paths=_manifest_paths(),
                    env=_env(tmp_path),
                    runner=_noop_runner,
                )

        err = exc_info.value
        assert err.tool == "forge", f"WireError.tool should be 'forge', got {err.tool!r}"
        assert err.stage == "compose", (
            f"WireError.stage should be 'compose', got {err.stage!r}"
        )
        assert isinstance(err.__cause__, RuntimeError)

    def test_already_wired_tool_stays_committed_after_later_failure(self, tmp_path):
        """C-1.2/I-1: lore stays wired when forge fails — best-effort sequential."""
        import trailhead.wire as wire_mod
        from trailhead.wire import WireError, wire

        original_compose_plan = wire_mod.compose_plan

        def forge_failing_plan(manifest, caps, dest):
            if manifest.tool_name == "forge":
                raise RuntimeError("forge compose exploded")
            return original_compose_plan(manifest, caps, dest)

        # Ensure lore is processed before forge by passing ordered dict
        selection = {"lore": {"capture"}, "forge": {"planning"}}
        with patch.object(wire_mod, "compose_plan", side_effect=forge_failing_plan):
            with pytest.raises(WireError):
                wire(
                    selection,
                    manifest_paths=_manifest_paths(),
                    env=_env(tmp_path),
                    runner=_noop_runner,
                )

        # lore dest must be fully wired (lore was processed first)
        lore_dest = tmp_path / "composed" / "lore" / "plugins" / "lore"
        assert lore_dest.exists(), (
            "I-1: lore dest gone after forge failure — best-effort sequential violated"
        )

    def test_no_orphaned_staging_dir_after_wire_error(self, tmp_path):
        """C-1.2: WireError raised after forge failure leaves no forge staging dir."""
        import trailhead.wire as wire_mod
        from trailhead.wire import WireError, wire

        original_compose_plan = wire_mod.compose_plan

        def forge_failing_plan(manifest, caps, dest):
            if manifest.tool_name == "forge":
                raise RuntimeError("forge compose exploded")
            return original_compose_plan(manifest, caps, dest)

        with patch.object(wire_mod, "compose_plan", side_effect=forge_failing_plan):
            with pytest.raises(WireError):
                wire(
                    {"lore": {"capture"}, "forge": {"planning"}},
                    manifest_paths=_manifest_paths(),
                    env=_env(tmp_path),
                    runner=_noop_runner,
                )

        forge_plugins_dir = tmp_path / "composed" / "forge" / "plugins"
        if forge_plugins_dir.exists():
            leftover = list(forge_plugins_dir.glob("_forge_staging_*"))
            assert leftover == [], (
                f"orphaned forge staging dirs after WireError: {leftover}"
            )

    def test_wire_error_register_stage(self, tmp_path):
        """WireError names stage='register' when the runner raises on install."""
        from trailhead.wire import WireError, wire

        call_count = {"n": 0}

        def failing_on_install(args, **kwargs):
            if "install" in args:
                raise RuntimeError("install failed")

        with pytest.raises(WireError) as exc_info:
            wire(
                {"lore": {"capture"}},
                manifest_paths=_manifest_paths(),
                env=_env(tmp_path),
                runner=failing_on_install,
            )

        err = exc_info.value
        assert err.tool == "lore"
        assert err.stage == "register"
        assert isinstance(err.__cause__, RuntimeError)


# ---------------------------------------------------------------------------
# T-W10: C-2 — registration-state marker; re-attempt register (not rewire)
#         for a tool whose dir exists but whose marker is absent.
# ---------------------------------------------------------------------------


class TestRegistrationMarker:
    def test_marker_written_after_successful_register(self, tmp_path):
        """C-2: .trailhead-registered marker exists after a successful wire."""
        from trailhead.wire import wire

        wire(
            {"lore": {"capture"}},
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        mkt_root = tmp_path / "composed" / "lore"
        assert (mkt_root / ".trailhead-registered").exists(), (
            "C-2: .trailhead-registered marker absent after successful wire"
        )

    def test_marker_absent_after_failed_register(self, tmp_path):
        """C-2: marker is NOT written when register fails mid-way."""
        from trailhead.wire import WireError, wire

        def failing_on_install(args, **kwargs):
            if "install" in args:
                raise RuntimeError("install failed")

        with pytest.raises(WireError):
            wire(
                {"lore": {"capture"}},
                manifest_paths=_manifest_paths(),
                env=_env(tmp_path),
                runner=failing_on_install,
            )

        mkt_root = tmp_path / "composed" / "lore"
        assert not (mkt_root / ".trailhead-registered").exists(), (
            "C-2: marker written despite register failure"
        )

    def test_second_wire_without_marker_calls_register_not_rewire(self, tmp_path):
        """C-2: dir exists but marker absent → register (not rewire) is called.

        Simulates a partially-registered tool: promote succeeded, but register
        failed before marker was written.  Next wire must self-heal via register,
        not call `plugin update` (which would fail on a never-installed plugin).
        """
        from trailhead.wire import wire

        # First wire: succeed to create the dir, then wipe the marker to simulate
        # a half-registered state.
        calls = []

        def recording_runner(args, **kwargs):
            calls.append(list(args))

        wire(
            {"lore": {"capture"}},
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=recording_runner,
        )
        # Wipe the marker to simulate promote-succeeded-but-register-failed
        marker = tmp_path / "composed" / "lore" / ".trailhead-registered"
        marker.unlink()
        calls.clear()

        # Second wire: dir exists but no marker → must call register (add+install)
        wire(
            {"lore": {"capture"}},
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=recording_runner,
        )

        update_calls = [c for c in calls if "update" in c]
        install_calls = [c for c in calls if "install" in c]
        assert update_calls == [], (
            f"C-2: rewire called on half-registered tool (should have called register): {update_calls}"
        )
        assert len(install_calls) >= 1, (
            f"C-2: register (install) not called for half-registered tool: {calls}"
        )

    def test_second_wire_with_marker_calls_rewire(self, tmp_path):
        """C-2: dir + marker present → rewire (plugin update) is called."""
        from trailhead.wire import wire

        calls = []

        def recording_runner(args, **kwargs):
            calls.append(list(args))

        # First wire: fully successful — dir + marker both created
        wire(
            {"lore": {"capture"}},
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=recording_runner,
        )
        calls.clear()

        # Second wire: fully registered → rewire path
        wire(
            {"lore": {"capture"}},
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=recording_runner,
        )

        update_calls = [c for c in calls if "update" in c]
        assert len(update_calls) >= 1, (
            f"C-2: rewire (plugin update) not called for fully-registered tool: {calls}"
        )


# ---------------------------------------------------------------------------
# T-W11: Minor-2 — cross-tool dest collision is structurally unreachable.
#         Each tool composes into composed/<tool>/plugins/<tool>, so two tools
#         can never collide.  This test documents that structural guarantee.
# ---------------------------------------------------------------------------


class TestCrossToolCollisionUnreachable:
    def test_each_tool_has_distinct_mkt_root(self, tmp_path):
        """Minor-2: each tool's mkt_root is distinct (composed/<tool>/).

        Cross-tool dest collision is structurally unreachable: tool A writes to
        composed/A/plugins/A and tool B writes to composed/B/plugins/B.
        Two different tools can never share the same dest path.
        """
        from trailhead.wire import wire

        selection = {
            "lore": {"capture", "recall"},
            "forge": {"planning", "helpers"},
        }
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )

        lore_dest = tmp_path / "composed" / "lore" / "plugins" / "lore"
        forge_dest = tmp_path / "composed" / "forge" / "plugins" / "forge"
        assert lore_dest.exists()
        assert forge_dest.exists()
        # The two live dests are rooted under different mkt_roots — no collision possible
        assert not lore_dest.is_relative_to(forge_dest), (
            "Minor-2: lore dest is under forge dest (collision!)"
        )
        assert not forge_dest.is_relative_to(lore_dest), (
            "Minor-2: forge dest is under lore dest (collision!)"
        )
