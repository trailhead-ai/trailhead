"""Tests for trailhead/wire.py — shared-root multi-tool orchestrator (Slice 3).

TDD: rewritten for the consolidated single-marketplace layout (Slice 3).

Consolidated layout (Slice 3):
  - mkt_root = composed_root (shared across all tools), computed once.
  - Each tool's plugin tree lands at composed_root/plugins/<tool>/ via
    staging + atomic promote.
  - ONE marketplace.json at composed_root/.claude-plugin/marketplace.json,
    name == "trailhead", plugins[] = the tools that promoted SUCCESSFULLY
    this run (on-disk truth).
  - Split markers in composed_root: a global .trailhead-registered plus
    per-tool .trailhead-installed-<tool>.
  - Registration sequencing lives in the wire() loop, NOT in _compose_tool:
    after the compose loop, generate_marketplace_json once, register_marketplace
    once, then per-tool install_tool (marker absent) or rewire_tool (present).

Contract preserved from prior slices:
  - The harness CLI runner is stubbed in all tests (B-3 hermeticity).
  - S-2: apply_plan(mode="copy") — no symlinks in composed tree.
  - R-1: staging-dir + atomic promote — a mid-compose failure leaves the
    prior dest unchanged.
  - C-1.1: staging cleanup runs on ANY exception (try/finally).
  - C-1.2/I-1: WireError names tool + stage; multi-tool wire is best-effort
    sequential.
  - B-5: minimal preset → no camp/craft dests.

New to Slice 3:
  - Consolidated marketplace.json content: name + multi-tool plugins[].
  - On-disk-truth blast-radius: a failed tool is absent from plugins[] while
    an earlier-wired tool is present (content-level assertion).
  - register_marketplace invoked ONCE across a multi-tool wire; install/rewire
    per tool; install-vs-rewire keyed on the per-tool marker.
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
_FORGE_MANIFEST = _REPO_ROOT / "tools" / "craft" / "capabilities.toml"


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
        "craft": _FORGE_MANIFEST,
    }


def _live_dest(tmp_path: Path, tool: str) -> Path:
    """Consolidated layout: composed/plugins/<tool>."""
    return tmp_path / "composed" / "plugins" / tool


def _marketplace_json(tmp_path: Path) -> Path:
    return tmp_path / "composed" / ".claude-plugin" / "marketplace.json"


# ---------------------------------------------------------------------------
# T-W1: minimal preset — lore only, no camp/craft dests (B-5 enforcement)
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
        assert _live_dest(tmp_path, "lore").exists(), "lore plugin dest missing after minimal wire"

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
        assert not _live_dest(tmp_path, "camp").exists(), (
            "camp dest exists despite minimal preset (B-5 violation)"
        )

    def test_minimal_no_craft_dest(self, tmp_path):
        """wire with minimal preset creates NO craft dest (B-5)."""
        from trailhead.wire import wire

        selection = {"lore": {"capture", "recall", "sessions"}}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        assert not _live_dest(tmp_path, "craft").exists(), (
            "craft dest exists despite minimal preset (B-5 violation)"
        )

    def test_minimal_marketplace_lists_only_lore(self, tmp_path):
        """plugins[] = the successfully-wired set; only lore here."""
        from trailhead.wire import wire

        selection = {"lore": {"capture"}}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        data = json.loads(_marketplace_json(tmp_path).read_text())
        names = {p["name"] for p in data["plugins"]}
        assert names == {"lore"}, f"expected only lore in plugins[], got {names}"


# ---------------------------------------------------------------------------
# T-W2: standard preset — lore + camp + craft dests under shared root
# ---------------------------------------------------------------------------


class TestStandardPreset:
    def _wire_standard(self, tmp_path):
        from trailhead.wire import wire

        selection = {
            "lore": {"capture", "recall", "sessions"},
            "camp": set(),
            "craft": {"planning", "execute", "review", "helpers"},
        }
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )

    def test_standard_lore_dest_exists(self, tmp_path):
        self._wire_standard(tmp_path)
        assert _live_dest(tmp_path, "lore").exists()

    def test_standard_camp_dest_exists(self, tmp_path):
        self._wire_standard(tmp_path)
        assert _live_dest(tmp_path, "camp").exists()

    def test_standard_craft_dest_exists(self, tmp_path):
        self._wire_standard(tmp_path)
        assert _live_dest(tmp_path, "craft").exists()

    def test_standard_craft_council_agents_absent(self, tmp_path):
        """council agents absent when craft wired without council."""
        from trailhead.capabilities import load_manifest
        from trailhead.wire import wire

        selection = {
            "lore": {"capture", "recall", "sessions"},
            "camp": set(),
            "craft": {"planning", "execute", "review", "helpers"},
        }
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        craft_manifest = load_manifest(_FORGE_MANIFEST)
        craft_dest = _live_dest(tmp_path, "craft")
        council_agents = craft_manifest.capabilities["council"]["agents"]
        assert len(council_agents) > 0, "test is vacuous: council has no agents"
        for agent in council_agents:
            assert not (craft_dest / agent).exists(), (
                f"council agent {agent!r} present in craft dest despite council not selected"
            )

    def test_standard_craft_unselected_council_and_execute_absent(self, tmp_path):
        """Agents from unselected council/execute must not appear in the wired dest."""
        from trailhead.capabilities import load_manifest
        from trailhead.wire import wire

        selection = {
            "lore": {"capture", "recall", "sessions"},
            "camp": set(),
            "craft": {"planning", "review", "helpers"},
        }
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        craft_manifest = load_manifest(_FORGE_MANIFEST)
        craft_dest = _live_dest(tmp_path, "craft")
        for absent_cap in ("council", "execute"):
            agents = craft_manifest.capabilities[absent_cap]["agents"]
            assert len(agents) > 0, f"test is vacuous: {absent_cap} has no agents"
            for agent in agents:
                assert not (craft_dest / agent).exists(), (
                    f"{absent_cap} agent {agent!r} present in craft dest despite "
                    f"{absent_cap} not being selected"
                )

    def test_lore_shared_vaults_absent_in_standard(self, tmp_path):
        """lore shared-vaults absent in standard."""
        from trailhead.capabilities import load_manifest
        from trailhead.wire import wire

        selection = {
            "lore": {"capture", "recall", "sessions"},
            "camp": set(),
            "craft": {"planning", "execute", "review", "helpers"},
        }
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        lore_manifest = load_manifest(_LORE_MANIFEST)
        lore_dest = _live_dest(tmp_path, "lore")
        for skill in lore_manifest.capabilities["shared-vaults"]["skills"]:
            assert not (lore_dest / skill).exists()


# ---------------------------------------------------------------------------
# T-W3: consolidated marketplace.json — name == "trailhead", multi-tool plugins[]
# ---------------------------------------------------------------------------


class TestConsolidatedMarketplace:
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
        plugin_json = _live_dest(tmp_path, "lore") / ".claude-plugin" / "plugin.json"
        assert plugin_json.exists()
        data = json.loads(plugin_json.read_text())
        assert isinstance(data, dict)

    def test_single_consolidated_marketplace_named_trailhead(self, tmp_path):
        """ONE marketplace.json at composed/.claude-plugin/, name == 'trailhead'."""
        from trailhead.wire import wire

        selection = {"lore": {"capture"}}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        mkt_json = _marketplace_json(tmp_path)
        assert mkt_json.exists()
        data = json.loads(mkt_json.read_text())
        assert data["name"] == "trailhead"
        assert any(p["name"] == "lore" for p in data["plugins"])

    def test_marketplace_lists_both_tools_after_multi_wire(self, tmp_path):
        """After wiring {lore, camp}: plugins[] lists BOTH; trees exist for both."""
        from trailhead.wire import wire

        selection = {"lore": {"capture"}, "camp": set()}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        data = json.loads(_marketplace_json(tmp_path).read_text())
        assert data["name"] == "trailhead"
        names = {p["name"] for p in data["plugins"]}
        assert names == {"lore", "camp"}, f"expected both lore+camp, got {names}"
        assert _live_dest(tmp_path, "lore").exists()
        assert _live_dest(tmp_path, "camp").exists()

    def test_no_per_tool_marketplace_dirs(self, tmp_path):
        """The old per-tool composed/<tool>/ marketplace dirs must NOT be created."""
        from trailhead.wire import wire

        selection = {"lore": {"capture"}, "camp": set()}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        assert not (tmp_path / "composed" / "lore" / ".claude-plugin").exists()
        assert not (tmp_path / "composed" / "camp" / ".claude-plugin").exists()

    def test_s2_no_symlinks_in_wired_tree(self, tmp_path):
        """S-2: the composed tree must contain no symlinks."""
        from trailhead.wire import wire

        selection = {"lore": {"capture", "recall"}, "craft": {"planning", "helpers"}}
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
        plugin_dest = _live_dest(tmp_path, "lore")
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

        selection_full = {"lore": {"capture", "recall", "sessions"}}
        wire(
            selection_full,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        plugin_dest = _live_dest(tmp_path, "lore")
        assert (plugin_dest / "agents" / "librarian.md").exists()

        selection_narrow = {"lore": {"capture"}}
        wire(
            selection_narrow,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
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

        selection = {"lore": {"capture"}}
        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        plugin_dest = _live_dest(tmp_path, "lore")
        assert plugin_dest.exists()

        before_files = {
            str(p.relative_to(plugin_dest))
            for p in plugin_dest.rglob("*")
            if p.is_file()
        }

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
        plugin_dest = _live_dest(tmp_path, "lore")
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

        assert not plugin_dest.exists(), (
            "R-1 violated: partial dest exists after failed first wire"
        )


# ---------------------------------------------------------------------------
# T-W6: registry sequencing — register_marketplace ONCE, install/rewire per tool
# ---------------------------------------------------------------------------


class TestRegistrySequencing:
    def test_marketplace_add_invoked_once_across_multi_tool_wire(self, tmp_path):
        """register_marketplace runs ONCE (global), not once per tool."""
        from trailhead.wire import wire

        selection = {"lore": {"capture"}, "craft": {"planning"}}
        calls = []

        def stub_runner(args, **kwargs):
            calls.append(list(args))

        wire(
            selection,
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=stub_runner,
        )
        marketplace_adds = [c for c in calls if "marketplace" in c and "add" in c]
        installs = [c for c in calls if "install" in c]
        assert len(marketplace_adds) == 1, (
            f"expected exactly ONE marketplace add across the wire, got {marketplace_adds}"
        )
        assert len(installs) == 2, (
            f"expected 2 install calls (lore+craft), got {installs}"
        )

    def test_install_references_tool_at_trailhead(self, tmp_path):
        """install call references <tool>@trailhead (NOT @trailhead-<tool>)."""
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
        installs = [c for c in calls if "install" in c]
        assert any("lore@trailhead" in c for c in installs), (
            f"expected 'lore@trailhead' in an install call, got {installs}"
        )
        assert not any("lore@trailhead-lore" in c for c in installs), (
            f"unexpected per-tool marketplace ref in install: {installs}"
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

    def test_marketplace_add_includes_composed_root(self, tmp_path):
        """marketplace add call includes the shared composed_root path."""
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
        expected_root = str(tmp_path / "composed")
        assert expected_root in add_calls[0], (
            f"expected composed_root {expected_root!r} in add call: {add_calls[0]}"
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
        lore_dest = _live_dest(tmp_path, "lore")
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
        lore_dest = _live_dest(tmp_path, "lore")
        assert (lore_dest / "agents" / "librarian.md").exists(), (
            "librarian.md missing from minimal lore dest"
        )


# ---------------------------------------------------------------------------
# T-W8: C-1.1 — staging cleanup runs on BaseException (try/finally)
# ---------------------------------------------------------------------------


class TestStagingCleanupOnBaseException:
    def test_staging_dir_cleaned_on_keyboard_interrupt(self, tmp_path):
        """C-1.1: a KeyboardInterrupt mid-compose must leave no staging dir."""
        import trailhead.wire as wire_mod
        from trailhead.wire import wire

        staging_parent = tmp_path / "composed" / "plugins"

        def raising_compose_plan(manifest, caps, dest):
            raise KeyboardInterrupt("simulated interrupt")

        with patch.object(wire_mod, "compose_plan", side_effect=raising_compose_plan):
            with pytest.raises((KeyboardInterrupt, Exception)):
                wire(
                    {"lore": {"capture"}},
                    manifest_paths=_manifest_paths(),
                    env=_env(tmp_path),
                    runner=_noop_runner,
                )

        if staging_parent.exists():
            leftover = list(staging_parent.glob("_lore_staging_*"))
            assert leftover == [], (
                f"C-1.1 violated: staging dirs orphaned after KeyboardInterrupt: {leftover}"
            )

    def test_staging_dir_cleaned_on_system_exit(self, tmp_path):
        """C-1.1: a SystemExit mid-compose must leave no staging dir."""
        import trailhead.wire as wire_mod
        from trailhead.wire import wire

        staging_parent = tmp_path / "composed" / "plugins"

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
        staging_parent = tmp_path / "composed" / "plugins"
        leftover = list(staging_parent.glob("_lore_staging_*"))
        assert leftover == [], (
            f"staging dir not cleaned after successful wire: {leftover}"
        )


# ---------------------------------------------------------------------------
# T-W9: C-1.2/I-1 — WireError names the failing tool + stage; best-effort
#        sequential; on-disk-truth blast-radius isolation.
# ---------------------------------------------------------------------------


class TestWireErrorIsolation:
    def test_wire_error_raised_naming_failing_tool(self, tmp_path):
        """C-1.2: a per-tool failure raises WireError naming the tool."""
        import trailhead.wire as wire_mod
        from trailhead.wire import WireError, wire

        original_compose_plan = wire_mod.compose_plan

        def craft_failing_plan(manifest, caps, dest):
            if manifest.tool_name == "craft":
                raise RuntimeError("craft compose exploded")
            return original_compose_plan(manifest, caps, dest)

        with patch.object(wire_mod, "compose_plan", side_effect=craft_failing_plan):
            with pytest.raises(WireError) as exc_info:
                wire(
                    {"lore": {"capture"}, "craft": {"planning"}},
                    manifest_paths=_manifest_paths(),
                    env=_env(tmp_path),
                    runner=_noop_runner,
                )

        err = exc_info.value
        assert err.tool == "craft", f"WireError.tool should be 'craft', got {err.tool!r}"
        assert err.stage == "compose", (
            f"WireError.stage should be 'compose', got {err.stage!r}"
        )
        assert isinstance(err.__cause__, RuntimeError)

    def test_already_wired_tool_stays_committed_after_later_failure(self, tmp_path):
        """C-1.2/I-1: lore stays wired when craft fails — best-effort sequential."""
        import trailhead.wire as wire_mod
        from trailhead.wire import WireError, wire

        original_compose_plan = wire_mod.compose_plan

        def craft_failing_plan(manifest, caps, dest):
            if manifest.tool_name == "craft":
                raise RuntimeError("craft compose exploded")
            return original_compose_plan(manifest, caps, dest)

        selection = {"lore": {"capture"}, "craft": {"planning"}}
        with patch.object(wire_mod, "compose_plan", side_effect=craft_failing_plan):
            with pytest.raises(WireError):
                wire(
                    selection,
                    manifest_paths=_manifest_paths(),
                    env=_env(tmp_path),
                    runner=_noop_runner,
                )

        assert _live_dest(tmp_path, "lore").exists(), (
            "I-1: lore dest gone after craft failure — best-effort sequential violated"
        )

    def test_no_orphaned_staging_dir_after_wire_error(self, tmp_path):
        """C-1.2: WireError after craft failure leaves no craft staging dir."""
        import trailhead.wire as wire_mod
        from trailhead.wire import WireError, wire

        original_compose_plan = wire_mod.compose_plan

        def craft_failing_plan(manifest, caps, dest):
            if manifest.tool_name == "craft":
                raise RuntimeError("craft compose exploded")
            return original_compose_plan(manifest, caps, dest)

        with patch.object(wire_mod, "compose_plan", side_effect=craft_failing_plan):
            with pytest.raises(WireError):
                wire(
                    {"lore": {"capture"}, "craft": {"planning"}},
                    manifest_paths=_manifest_paths(),
                    env=_env(tmp_path),
                    runner=_noop_runner,
                )

        plugins_dir = tmp_path / "composed" / "plugins"
        if plugins_dir.exists():
            leftover = list(plugins_dir.glob("_craft_staging_*"))
            assert leftover == [], (
                f"orphaned craft staging dirs after WireError: {leftover}"
            )

    def test_failed_tool_absent_from_marketplace_but_earlier_tool_present(self, tmp_path):
        """On-disk-truth blast-radius (content-level): a tool whose compose raises is
        OMITTED from the regenerated marketplace.json plugins[], while an
        earlier-wired tool IS listed.

        This is the load-bearing isolation proof: one bad tool must never appear
        in plugins[] and break validation for the others.
        """
        import trailhead.wire as wire_mod
        from trailhead.wire import WireError, wire

        original_compose_plan = wire_mod.compose_plan

        def craft_failing_plan(manifest, caps, dest):
            if manifest.tool_name == "craft":
                raise RuntimeError("craft compose exploded")
            return original_compose_plan(manifest, caps, dest)

        # lore is processed before craft (ordered dict).
        selection = {"lore": {"capture"}, "craft": {"planning"}}
        with patch.object(wire_mod, "compose_plan", side_effect=craft_failing_plan):
            with pytest.raises(WireError):
                wire(
                    selection,
                    manifest_paths=_manifest_paths(),
                    env=_env(tmp_path),
                    runner=_noop_runner,
                )

        mkt_json = _marketplace_json(tmp_path)
        assert mkt_json.exists(), (
            "marketplace.json must be regenerated even after a per-tool failure "
            "so the surviving tool is registered"
        )
        data = json.loads(mkt_json.read_text())
        names = {p["name"] for p in data["plugins"]}
        assert "lore" in names, (
            f"earlier-wired lore missing from plugins[] (blast-radius leaked): {names}"
        )
        assert "craft" not in names, (
            f"failed craft present in plugins[] — would fail validation for ALL: {names}"
        )

    def test_wire_error_register_stage(self, tmp_path):
        """WireError names stage='register' when the runner raises on install."""
        from trailhead.wire import WireError, wire

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
# T-W10: split markers — global register marker + per-tool install marker;
#        install-vs-rewire keyed on the per-tool marker.
# ---------------------------------------------------------------------------


class TestSplitMarkers:
    def test_global_and_per_tool_markers_written_after_success(self, tmp_path):
        """Global .trailhead-registered + per-tool .trailhead-installed-<tool> present."""
        from trailhead.wire import wire

        wire(
            {"lore": {"capture"}},
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=_noop_runner,
        )
        composed_root = tmp_path / "composed"
        assert (composed_root / ".trailhead-registered").exists(), (
            "global .trailhead-registered marker absent after successful wire"
        )
        assert (composed_root / ".trailhead-installed-lore").exists(), (
            "per-tool .trailhead-installed-lore marker absent after successful wire"
        )

    def test_install_marker_absent_after_failed_install(self, tmp_path):
        """Per-tool install marker is NOT written when install fails."""
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

        composed_root = tmp_path / "composed"
        assert not (composed_root / ".trailhead-installed-lore").exists(), (
            "per-tool install marker written despite install failure"
        )

    def test_second_wire_without_marker_calls_install_not_rewire(self, tmp_path):
        """Per-tool marker absent → install_tool (NOT rewire/uninstall) is called."""
        from trailhead.wire import wire

        calls = []

        def recording_runner(args, **kwargs):
            calls.append(list(args))

        wire(
            {"lore": {"capture"}},
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=recording_runner,
        )
        # Wipe the per-tool marker to simulate promote-succeeded-but-install-failed.
        marker = tmp_path / "composed" / ".trailhead-installed-lore"
        marker.unlink()
        calls.clear()

        wire(
            {"lore": {"capture"}},
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=recording_runner,
        )

        uninstall_calls = [c for c in calls if "uninstall" in c]
        install_calls = [c for c in calls if "install" in c and "uninstall" not in c]
        assert uninstall_calls == [], (
            f"rewire (uninstall) called on un-installed tool; should have installed: {uninstall_calls}"
        )
        assert len(install_calls) >= 1, (
            f"install not called for un-installed tool: {calls}"
        )

    def test_second_wire_with_marker_calls_rewire(self, tmp_path):
        """Per-tool marker present → rewire_tool (uninstall + install) is called."""
        from trailhead.wire import wire

        calls = []

        def recording_runner(args, **kwargs):
            calls.append(list(args))

        wire(
            {"lore": {"capture"}},
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=recording_runner,
        )
        calls.clear()

        wire(
            {"lore": {"capture"}},
            manifest_paths=_manifest_paths(),
            env=_env(tmp_path),
            runner=recording_runner,
        )

        uninstall_calls = [c for c in calls if "uninstall" in c]
        assert len(uninstall_calls) >= 1, (
            f"rewire (uninstall+install) not called for installed tool: {calls}"
        )
        # And it must NOT use plugin update (U-1(e)).
        update_calls = [c for c in calls if "update" in c]
        assert update_calls == [], (
            f"rewire must not use 'plugin update' (U-1(e)): {update_calls}"
        )
