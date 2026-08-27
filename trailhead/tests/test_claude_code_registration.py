"""Tests for the Claude Code harness registration tail (trailhead/harness/claude_code.py).

Exercises the Shape-A ``marketplace.json`` writer, the ``claude plugin …`` CLI
calls, the on-disk registration markers, and the tool-name input guard — all
through the generic :class:`~trailhead.harness.base.Harness` surface on
:class:`~trailhead.harness.claude_code.ClaudeCodeHarness`.

Contract (HERMETICITY):
  - generate_manifest writes ONE marketplace.json at
    <composed_root>/.claude-plugin/marketplace.json with name "trailhead", one
    plugins[] entry per tool, deterministic order, atomic write.
  - register shells marketplace add once, writes the global marker.
  - install_tool shells install <tool>@trailhead, writes the per-tool marker.
  - rewire_tool shells uninstall THEN install (NOT plugin update).
  - The harness-CLI invocation is injectable/patchable — tests NEVER invoke the
    real `claude plugin` CLI.
  - Input guard: ^[a-z][a-z0-9_-]*$ on every tool name before CLI/path use.
  - Never writes to ~/.claude/plugins/ — only writes marketplace.json under
    composed_root and shells the CLI (which the test stubs).
"""

import inspect
import json
import subprocess
from unittest.mock import patch

import pytest

from trailhead.harness.base import HarnessError
from trailhead.harness.claude_code import ClaudeCodeHarness

from .conftest import capturing_runner


def _harness():
    return ClaudeCodeHarness()


# ---------------------------------------------------------------------------
# generate_manifest — consolidated single marketplace
# ---------------------------------------------------------------------------


class TestGenerateManifest:
    def test_marketplace_json_written(self, composed_root):
        _harness().generate_manifest(["lore"], composed_root)
        assert (composed_root / ".claude-plugin" / "marketplace.json").exists()

    def test_marketplace_name_is_trailhead(self, composed_root):
        """Consolidated marketplace name must be 'trailhead', not 'trailhead-<tool>'."""
        _harness().generate_manifest(["lore"], composed_root)
        data = json.loads((composed_root / ".claude-plugin" / "marketplace.json").read_text())
        assert data["name"] == "trailhead"

    def test_marketplace_owner_name_is_trailhead(self, composed_root):
        _harness().generate_manifest(["lore"], composed_root)
        data = json.loads((composed_root / ".claude-plugin" / "marketplace.json").read_text())
        assert data["owner"] == {"name": "trailhead"}

    def test_multi_tool_plugins_list(self, composed_root):
        _harness().generate_manifest(["lore", "camp"], composed_root)
        data = json.loads((composed_root / ".claude-plugin" / "marketplace.json").read_text())
        assert len(data["plugins"]) == 2

    def test_plugins_contain_correct_names(self, composed_root):
        _harness().generate_manifest(["lore", "camp"], composed_root)
        data = json.loads((composed_root / ".claude-plugin" / "marketplace.json").read_text())
        plugin_names = [p["name"] for p in data["plugins"]]
        assert "lore" in plugin_names
        assert "camp" in plugin_names

    def test_plugin_source_relative_to_plugins_subdir(self, composed_root):
        """Each plugin source must be './plugins/<tool>' (relative, Shape A)."""
        _harness().generate_manifest(["lore", "camp"], composed_root)
        data = json.loads((composed_root / ".claude-plugin" / "marketplace.json").read_text())
        for plugin in data["plugins"]:
            tool = plugin["name"]
            assert plugin["source"] == f"./plugins/{tool}"

    def test_plugins_list_is_deterministic(self, composed_root):
        """plugins[] order must be deterministic (sorted) across calls."""
        _harness().generate_manifest(["camp", "lore"], composed_root)
        data_a = json.loads((composed_root / ".claude-plugin" / "marketplace.json").read_text())
        _harness().generate_manifest(["lore", "camp"], composed_root)
        data_b = json.loads((composed_root / ".claude-plugin" / "marketplace.json").read_text())
        assert [p["name"] for p in data_a["plugins"]] == [p["name"] for p in data_b["plugins"]]

    def test_single_tool_is_valid(self, composed_root):
        _harness().generate_manifest(["craft"], composed_root)
        data = json.loads((composed_root / ".claude-plugin" / "marketplace.json").read_text())
        assert data["name"] == "trailhead"
        assert len(data["plugins"]) == 1
        assert data["plugins"][0]["name"] == "craft"

    def test_three_tools_all_present(self, composed_root):
        _harness().generate_manifest(["lore", "camp", "craft"], composed_root)
        data = json.loads((composed_root / ".claude-plugin" / "marketplace.json").read_text())
        assert len(data["plugins"]) == 3
        assert {p["name"] for p in data["plugins"]} == {"lore", "camp", "craft"}

    def test_claude_plugin_dir_created_automatically(self, composed_root):
        claude_dir = composed_root / ".claude-plugin"
        assert not claude_dir.exists()
        _harness().generate_manifest(["lore"], composed_root)
        assert claude_dir.exists()

    def test_atomic_write_no_partial_file(self, composed_root):
        """Write must be atomic: the destination is valid JSON after the call."""
        _harness().generate_manifest(["lore", "camp"], composed_root)
        out = composed_root / ".claude-plugin" / "marketplace.json"
        data = json.loads(out.read_text())
        assert isinstance(data, dict)
        assert data["name"] == "trailhead"

    def test_plugin_entries_have_description(self, composed_root):
        _harness().generate_manifest(["lore", "camp"], composed_root)
        data = json.loads((composed_root / ".claude-plugin" / "marketplace.json").read_text())
        for plugin in data["plugins"]:
            assert isinstance(plugin["description"], str)


# ---------------------------------------------------------------------------
# input guard — invalid tool names rejected before CLI/path use
# ---------------------------------------------------------------------------


class TestInputGuard:
    @pytest.mark.parametrize(
        "invalid_tool",
        [
            "Lore",  # uppercase
            "1lore",  # starts with digit
            "lore tool",  # space
            "lore/../etc",  # path traversal
            "",  # empty
            "lore@trailhead",  # special char
            "CAMP",  # all-caps
            "-lore",  # starts with hyphen
        ],
    )
    def test_generate_rejects_invalid_tool_names(self, composed_root, invalid_tool):
        with pytest.raises((ValueError, TypeError)):
            _harness().generate_manifest([invalid_tool], composed_root)

    @pytest.mark.parametrize("invalid_tool", ["Lore", "1lore", "lore tool", "", "-lore"])
    def test_install_tool_rejects_invalid_tool_names(self, composed_root, invalid_tool):
        with pytest.raises((ValueError, TypeError)):
            _harness().install_tool(invalid_tool, composed_root, runner=lambda args, **kw: None)

    @pytest.mark.parametrize("invalid_tool", ["Lore", "1lore", "lore tool", "", "-lore"])
    def test_rewire_tool_rejects_invalid_tool_names(self, composed_root, invalid_tool):
        with pytest.raises((ValueError, TypeError)):
            _harness().rewire_tool(invalid_tool, composed_root, runner=lambda args, **kw: None)

    @pytest.mark.parametrize(
        "valid_tool", ["lore", "camp", "craft", "lore-plugin", "tool123", "my-tool"]
    )
    def test_generate_accepts_valid_tool_names(self, composed_root, valid_tool):
        _harness().generate_manifest([valid_tool], composed_root)  # must not raise


# ---------------------------------------------------------------------------
# register — global marketplace add + global marker
# ---------------------------------------------------------------------------


class TestRegister:
    def test_calls_marketplace_add(self, composed_root):
        runner, calls_seen = capturing_runner()
        _harness().register(composed_root, runner=runner)
        add_calls = [args for args in calls_seen if "marketplace" in args and "add" in args]
        assert len(add_calls) == 1
        assert str(composed_root) in add_calls[0]

    def test_scope_user(self, composed_root):
        runner, calls_seen = capturing_runner()
        _harness().register(composed_root, runner=runner)
        for call_args in calls_seen:
            assert call_args[call_args.index("--scope") + 1] == "user"

    def test_writes_global_marker_on_success(self, composed_root, claude_dir):
        _harness().register(composed_root, runner=lambda args, **kw: None)
        assert (claude_dir / ".trailhead-registered").exists()

    def test_no_marker_when_runner_raises(self, composed_root, claude_dir):
        def failing_runner(args, **kw):
            raise RuntimeError("marketplace add failed")

        with pytest.raises(RuntimeError):
            _harness().register(composed_root, runner=failing_runner)
        assert not (claude_dir / ".trailhead-registered").exists()

    def test_args_are_list(self, composed_root):
        runner, calls_seen = capturing_runner()
        _harness().register(composed_root, runner=runner)
        for call_args in calls_seen:
            assert isinstance(call_args, list)

    def test_never_invokes_real_cli(self, composed_root):
        with patch("subprocess.run") as mock_run:
            _harness().register(composed_root, runner=lambda args, **kw: None)
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# install_tool — per-tool install + per-tool marker
# ---------------------------------------------------------------------------


class TestInstallTool:
    def test_calls_plugin_install(self, composed_root):
        runner, calls_seen = capturing_runner()
        _harness().install_tool("lore", composed_root, runner=runner)
        assert len([args for args in calls_seen if "install" in args]) == 1

    def test_ref_is_trailhead_not_per_tool(self, composed_root):
        """Install ref must be '<tool>@trailhead', NOT '<tool>@trailhead-<tool>'."""
        runner, calls_seen = capturing_runner()
        _harness().install_tool("lore", composed_root, runner=runner)
        install_call = [args for args in calls_seen if "install" in args][0]
        assert "lore@trailhead" in install_call
        assert "lore@trailhead-lore" not in install_call

    def test_scope_user(self, composed_root):
        runner, calls_seen = capturing_runner()
        _harness().install_tool("lore", composed_root, runner=runner)
        for call_args in calls_seen:
            assert call_args[call_args.index("--scope") + 1] == "user"

    def test_writes_per_tool_marker_on_success(self, composed_root, claude_dir):
        _harness().install_tool("lore", composed_root, runner=lambda args, **kw: None)
        assert (claude_dir / ".trailhead-installed-lore").exists()

    def test_no_marker_when_runner_raises(self, composed_root, claude_dir):
        def failing_runner(args, **kw):
            raise RuntimeError("install failed")

        with pytest.raises(RuntimeError):
            _harness().install_tool("lore", composed_root, runner=failing_runner)
        assert not (claude_dir / ".trailhead-installed-lore").exists()

    def test_no_marker_when_runner_returns_a_nonzero_completed_process(
        self, composed_root, claude_dir
    ):
        """A `claude plugin install` that genuinely FAILS returns a nonzero
        CompletedProcess rather than raising — the injected runner here mirrors
        that shape exactly (not a raising stub) to prove install_tool inspects
        the returncode instead of only catching exceptions."""

        def failing_runner(args, **kw):
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="boom")

        with pytest.raises(HarnessError):
            _harness().install_tool("lore", composed_root, runner=failing_runner)
        assert not (claude_dir / ".trailhead-installed-lore").exists()

    def test_per_tool_markers_are_distinct(self, composed_root, claude_dir):
        _harness().install_tool("lore", composed_root, runner=lambda args, **kw: None)
        _harness().install_tool("camp", composed_root, runner=lambda args, **kw: None)
        assert (claude_dir / ".trailhead-installed-lore").exists()
        assert (claude_dir / ".trailhead-installed-camp").exists()

    def test_args_are_list(self, composed_root):
        runner, calls_seen = capturing_runner()
        _harness().install_tool("lore", composed_root, runner=runner)
        for call_args in calls_seen:
            assert isinstance(call_args, list)

    def test_never_invokes_real_cli(self, composed_root):
        with patch("subprocess.run") as mock_run:
            _harness().install_tool("lore", composed_root, runner=lambda args, **kw: None)
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# rewire_tool — uninstall THEN install (NOT plugin update)
# ---------------------------------------------------------------------------


class TestRewireTool:
    def test_calls_uninstall_then_install(self, composed_root):
        runner, calls_seen = capturing_runner()
        _harness().rewire_tool("lore", composed_root, runner=runner)
        verbs = []
        for call_args in calls_seen:
            if "uninstall" in call_args:
                verbs.append("uninstall")
            elif "install" in call_args:
                verbs.append("install")
        assert verbs == ["uninstall", "install"]

    def test_does_not_call_plugin_update(self, composed_root):
        runner, calls_seen = capturing_runner()
        _harness().rewire_tool("lore", composed_root, runner=runner)
        assert [args for args in calls_seen if "update" in args] == []

    def test_uses_trailhead_ref(self, composed_root):
        runner, calls_seen = capturing_runner()
        _harness().rewire_tool("lore", composed_root, runner=runner)
        for call_args in calls_seen:
            if "uninstall" in call_args or "install" in call_args:
                assert "lore@trailhead" in call_args
                assert "lore@trailhead-lore" not in call_args

    def test_tolerates_uninstall_failure(self, composed_root):
        install_calls = []

        def stub_runner(args, **kwargs):
            if "uninstall" in args:
                raise RuntimeError("not installed")
            if "install" in args:
                install_calls.append(list(args))

        _harness().rewire_tool("lore", composed_root, runner=stub_runner)
        assert len(install_calls) == 1

    def test_clears_per_tool_marker_before_pair(self, composed_root, claude_dir):
        marker = claude_dir / ".trailhead-installed-lore"
        marker.write_text("{}")
        marker_state_at_call_time = []
        _harness().rewire_tool(
            "lore", composed_root, runner=lambda args, **kw: marker_state_at_call_time.append(
                marker.exists()
            )
        )
        assert all(not present for present in marker_state_at_call_time)

    def test_rewrites_per_tool_marker_after_install(self, composed_root, claude_dir):
        _harness().rewire_tool("lore", composed_root, runner=lambda args, **kw: None)
        assert (claude_dir / ".trailhead-installed-lore").exists()

    def test_no_marker_when_install_raises(self, composed_root, claude_dir):
        marker = claude_dir / ".trailhead-installed-lore"
        marker.write_text("{}")

        def failing_on_install(args, **kw):
            if "install" in args:
                raise RuntimeError("install failed")

        with pytest.raises(RuntimeError):
            _harness().rewire_tool("lore", composed_root, runner=failing_on_install)
        assert not marker.exists()

    def test_no_marker_when_install_returns_a_nonzero_completed_process(
        self, composed_root, claude_dir
    ):
        """Same as install_tool's equivalent: a genuinely failing `claude plugin
        install` returns nonzero rather than raising, and rewire_tool must still
        detect it instead of writing the marker back."""
        marker = claude_dir / ".trailhead-installed-lore"
        marker.write_text("{}")

        def failing_on_install(args, **kw):
            if "install" in args:
                return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="boom")
            return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

        with pytest.raises(HarnessError):
            _harness().rewire_tool("lore", composed_root, runner=failing_on_install)
        assert not marker.exists()

    def test_scope_user_on_both_calls(self, composed_root):
        runner, calls_seen = capturing_runner()
        _harness().rewire_tool("lore", composed_root, runner=runner)
        for call_args in calls_seen:
            assert call_args[call_args.index("--scope") + 1] == "user"

    def test_never_invokes_real_subprocess(self, composed_root):
        with patch("subprocess.run") as mock_run:
            _harness().rewire_tool("lore", composed_root, runner=lambda args, **kw: None)
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# injectable runner — method signatures expose runner
# ---------------------------------------------------------------------------


class TestInjectableRunner:
    def test_register_has_injectable_runner(self):
        assert "runner" in inspect.signature(ClaudeCodeHarness.register).parameters

    def test_install_tool_has_injectable_runner(self):
        assert "runner" in inspect.signature(ClaudeCodeHarness.install_tool).parameters

    def test_rewire_tool_has_injectable_runner(self):
        assert "runner" in inspect.signature(ClaudeCodeHarness.rewire_tool).parameters


# ---------------------------------------------------------------------------
# Teardown — unregister_tool (per-tool) + unregister_marketplace (once, shared)
# ---------------------------------------------------------------------------


class TestUnregisterTool:
    def test_calls_plugin_uninstall_consolidated_ref(self, composed_root):
        runner, calls_seen = capturing_runner()
        _harness().unregister_tool("lore", composed_root, runner=runner)
        uninstall_calls = [a for a in calls_seen if "uninstall" in a]
        assert len(uninstall_calls) == 1
        assert "lore@trailhead" in uninstall_calls[0]
        assert "lore@trailhead-lore" not in uninstall_calls[0]

    def test_passes_keep_data_and_yes(self, composed_root):
        runner, calls_seen = capturing_runner()
        _harness().unregister_tool("lore", composed_root, runner=runner)
        call = calls_seen[0]
        assert "--keep-data" in call
        assert "--yes" in call
        assert call[call.index("--scope") + 1] == "user"

    def test_does_not_remove_marketplace(self, composed_root):
        runner, calls_seen = capturing_runner()
        _harness().unregister_tool("lore", composed_root, runner=runner)
        for call in calls_seen:
            assert not ("marketplace" in call and "remove" in call)

    def test_clears_per_tool_marker(self, composed_root, claude_dir):
        marker = claude_dir / ".trailhead-installed-lore"
        marker.write_text("{}")
        _harness().unregister_tool("lore", composed_root, runner=lambda args, **kw: None)
        assert not marker.exists()

    def test_clears_per_tool_marker_even_if_runner_raises(self, composed_root, claude_dir):
        marker = claude_dir / ".trailhead-installed-lore"
        marker.write_text("{}")

        def failing(args, **kw):
            raise RuntimeError("plugin not found")

        with pytest.raises(RuntimeError):
            _harness().unregister_tool("lore", composed_root, runner=failing)
        assert not marker.exists()

    def test_validates_tool_name(self, composed_root):
        with pytest.raises(ValueError):
            _harness().unregister_tool("../evil", composed_root, runner=lambda args, **kw: None)


class TestUnregisterMarketplace:
    def test_calls_marketplace_remove_trailhead(self, composed_root):
        runner, calls_seen = capturing_runner()
        _harness().unregister_marketplace(composed_root, runner=runner)
        assert len(calls_seen) == 1
        call = calls_seen[0]
        assert call[:5] == ["claude", "plugin", "marketplace", "remove", "trailhead"]
        assert call[call.index("--scope") + 1] == "user"
        assert "trailhead-lore" not in call

    def test_clears_global_marker(self, composed_root, claude_dir):
        marker = claude_dir / ".trailhead-registered"
        marker.write_text("{}")
        _harness().unregister_marketplace(composed_root, runner=lambda args, **kw: None)
        assert not marker.exists()

    def test_clears_global_marker_even_if_runner_raises(self, composed_root, claude_dir):
        marker = claude_dir / ".trailhead-registered"
        marker.write_text("{}")

        def failing(args, **kw):
            raise RuntimeError("marketplace not found")

        with pytest.raises(RuntimeError):
            _harness().unregister_marketplace(composed_root, runner=failing)
        assert not marker.exists()
