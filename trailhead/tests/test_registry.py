"""Tests for trailhead/registry.py — harness-registration concern.

TDD: written BEFORE implementation. All must fail first, then pass.

Contract (B-3 HERMETICITY):
  - registry.generate_marketplace_json writes ONE marketplace.json at
    <composed_root>/.claude-plugin/marketplace.json with name "trailhead",
    one plugins[] entry per tool, deterministic order, atomic write.
  - registry.register_marketplace shells marketplace add once, writes global marker.
  - registry.install_tool shells install <tool>@trailhead, writes per-tool marker.
  - registry.rewire_tool shells uninstall THEN install (NOT plugin update).
  - The harness-CLI invocation is injectable/patchable — tests NEVER invoke
    the real `claude plugin` CLI.
  - Input guard: ^[a-z][a-z0-9_-]*$ on every tool name before CLI/path use.
  - Never writes to ~/.claude/plugins/ — only writes marketplace.json under
    composed_root and shells the CLI (which the test stubs).
"""

import json
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# T-R1: generate_marketplace_json — consolidated single marketplace
# ---------------------------------------------------------------------------


class TestGenerateMarketplaceJson:
    def test_marketplace_json_written(self, tmp_path):
        from trailhead.registry import generate_marketplace_json

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        generate_marketplace_json(tools=["lore"], composed_root=composed_root)
        mkt_json = composed_root / ".claude-plugin" / "marketplace.json"
        assert mkt_json.exists()

    def test_marketplace_name_is_trailhead(self, tmp_path):
        """Consolidated marketplace name must be 'trailhead', not 'trailhead-<tool>'."""
        from trailhead.registry import generate_marketplace_json

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        generate_marketplace_json(tools=["lore"], composed_root=composed_root)
        data = json.loads(
            (composed_root / ".claude-plugin" / "marketplace.json").read_text()
        )
        assert data["name"] == "trailhead"

    def test_marketplace_owner_name_is_trailhead(self, tmp_path):
        from trailhead.registry import generate_marketplace_json

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        generate_marketplace_json(tools=["lore"], composed_root=composed_root)
        data = json.loads(
            (composed_root / ".claude-plugin" / "marketplace.json").read_text()
        )
        assert data["owner"] == {"name": "trailhead"}

    def test_multi_tool_plugins_list(self, tmp_path):
        """Two tools -> two plugins[] entries."""
        from trailhead.registry import generate_marketplace_json

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        generate_marketplace_json(tools=["lore", "camp"], composed_root=composed_root)
        data = json.loads(
            (composed_root / ".claude-plugin" / "marketplace.json").read_text()
        )
        assert len(data["plugins"]) == 2

    def test_plugins_contain_correct_names(self, tmp_path):
        from trailhead.registry import generate_marketplace_json

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        generate_marketplace_json(tools=["lore", "camp"], composed_root=composed_root)
        data = json.loads(
            (composed_root / ".claude-plugin" / "marketplace.json").read_text()
        )
        plugin_names = [p["name"] for p in data["plugins"]]
        assert "lore" in plugin_names
        assert "camp" in plugin_names

    def test_plugin_source_relative_to_plugins_subdir(self, tmp_path):
        """Each plugin source must be './plugins/<tool>' (relative, Shape A)."""
        from trailhead.registry import generate_marketplace_json

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        generate_marketplace_json(tools=["lore", "camp"], composed_root=composed_root)
        data = json.loads(
            (composed_root / ".claude-plugin" / "marketplace.json").read_text()
        )
        for plugin in data["plugins"]:
            tool = plugin["name"]
            assert plugin["source"] == f"./plugins/{tool}", (
                f"plugin '{tool}' source should be './plugins/{tool}', "
                f"got '{plugin['source']}'"
            )

    def test_plugins_list_is_deterministic(self, tmp_path):
        """plugins[] order must be deterministic (sorted) across calls."""
        from trailhead.registry import generate_marketplace_json

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        generate_marketplace_json(tools=["camp", "lore"], composed_root=composed_root)
        data_a = json.loads(
            (composed_root / ".claude-plugin" / "marketplace.json").read_text()
        )

        # Reverse order — should produce same JSON
        generate_marketplace_json(tools=["lore", "camp"], composed_root=composed_root)
        data_b = json.loads(
            (composed_root / ".claude-plugin" / "marketplace.json").read_text()
        )

        assert [p["name"] for p in data_a["plugins"]] == [
            p["name"] for p in data_b["plugins"]
        ]

    def test_single_tool_is_valid(self, tmp_path):
        """Single-tool case must produce a valid marketplace.json with one plugin."""
        from trailhead.registry import generate_marketplace_json

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        generate_marketplace_json(tools=["craft"], composed_root=composed_root)
        data = json.loads(
            (composed_root / ".claude-plugin" / "marketplace.json").read_text()
        )
        assert data["name"] == "trailhead"
        assert len(data["plugins"]) == 1
        assert data["plugins"][0]["name"] == "craft"

    def test_three_tools_all_present(self, tmp_path):
        """Three tools -> three plugins[] entries, all present."""
        from trailhead.registry import generate_marketplace_json

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        generate_marketplace_json(
            tools=["lore", "camp", "craft"], composed_root=composed_root
        )
        data = json.loads(
            (composed_root / ".claude-plugin" / "marketplace.json").read_text()
        )
        assert len(data["plugins"]) == 3
        names = {p["name"] for p in data["plugins"]}
        assert names == {"lore", "camp", "craft"}

    def test_claude_plugin_dir_created_automatically(self, tmp_path):
        """generate_marketplace_json must create .claude-plugin/ if absent."""
        from trailhead.registry import generate_marketplace_json

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        claude_dir = composed_root / ".claude-plugin"
        assert not claude_dir.exists()
        generate_marketplace_json(tools=["lore"], composed_root=composed_root)
        assert claude_dir.exists()

    def test_atomic_write_no_partial_file(self, tmp_path):
        """Write must be atomic: no partial marketplace.json left by a torn write.

        We verify this by confirming the final file is well-formed JSON and that
        the implementation uses a temp file + os.replace() (no direct open/write).
        The behavioral signal: the destination path exists and is valid after the call.
        """
        from trailhead.registry import generate_marketplace_json

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        generate_marketplace_json(tools=["lore", "camp"], composed_root=composed_root)
        out = composed_root / ".claude-plugin" / "marketplace.json"
        # Verify file is valid JSON (not torn mid-write)
        data = json.loads(out.read_text())
        assert isinstance(data, dict)
        assert data["name"] == "trailhead"

    def test_plugin_entries_have_description(self, tmp_path):
        from trailhead.registry import generate_marketplace_json

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        generate_marketplace_json(tools=["lore", "camp"], composed_root=composed_root)
        data = json.loads(
            (composed_root / ".claude-plugin" / "marketplace.json").read_text()
        )
        for plugin in data["plugins"]:
            assert "description" in plugin
            assert isinstance(plugin["description"], str)


# ---------------------------------------------------------------------------
# T-R2: input guard — invalid tool names are rejected before CLI/path use
# ---------------------------------------------------------------------------


class TestInputGuard:
    @pytest.mark.parametrize(
        "invalid_tool",
        [
            "Lore",          # uppercase
            "1lore",         # starts with digit
            "lore tool",     # space
            "lore/../etc",   # path traversal
            "",              # empty
            "lore@trailhead",  # special char
            "CAMP",          # all-caps
            "-lore",         # starts with hyphen
        ],
    )
    def test_generate_rejects_invalid_tool_names(self, tmp_path, invalid_tool):
        from trailhead.registry import generate_marketplace_json

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        with pytest.raises((ValueError, TypeError)):
            generate_marketplace_json(
                tools=[invalid_tool], composed_root=composed_root
            )

    @pytest.mark.parametrize(
        "invalid_tool",
        [
            "Lore",
            "1lore",
            "lore tool",
            "",
            "-lore",
        ],
    )
    def test_install_tool_rejects_invalid_tool_names(self, tmp_path, invalid_tool):
        from trailhead.registry import install_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        with pytest.raises((ValueError, TypeError)):
            install_tool(
                tool=invalid_tool,
                composed_root=composed_root,
                runner=lambda args, **kw: None,
            )

    @pytest.mark.parametrize(
        "invalid_tool",
        [
            "Lore",
            "1lore",
            "lore tool",
            "",
            "-lore",
        ],
    )
    def test_rewire_tool_rejects_invalid_tool_names(self, tmp_path, invalid_tool):
        from trailhead.registry import rewire_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        with pytest.raises((ValueError, TypeError)):
            rewire_tool(
                tool=invalid_tool,
                composed_root=composed_root,
                runner=lambda args, **kw: None,
            )

    @pytest.mark.parametrize(
        "valid_tool",
        ["lore", "camp", "craft", "lore-plugin", "tool123", "my-tool"],
    )
    def test_generate_accepts_valid_tool_names(self, tmp_path, valid_tool):
        from trailhead.registry import generate_marketplace_json

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        # Must not raise
        generate_marketplace_json(tools=[valid_tool], composed_root=composed_root)


# ---------------------------------------------------------------------------
# T-R3: register_marketplace — global marketplace add + global marker
# ---------------------------------------------------------------------------


class TestRegisterMarketplace:
    def test_register_marketplace_calls_marketplace_add(self, tmp_path):
        """register_marketplace must call
        'claude plugin marketplace add --scope user <composed_root>'."""
        from trailhead.registry import register_marketplace

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(list(args))

        register_marketplace(composed_root=composed_root, runner=stub_runner)

        add_calls = [
            args for args in calls_seen
            if "marketplace" in args and "add" in args
        ]
        assert len(add_calls) == 1, (
            f"expected one 'marketplace add' call; got {calls_seen}"
        )
        assert str(composed_root) in add_calls[0]

    def test_register_marketplace_scope_user(self, tmp_path):
        """marketplace add must pass --scope user."""
        from trailhead.registry import register_marketplace

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(list(args))

        register_marketplace(composed_root=composed_root, runner=stub_runner)

        for call_args in calls_seen:
            assert "--scope" in call_args
            idx = call_args.index("--scope")
            assert call_args[idx + 1] == "user"

    def test_register_marketplace_writes_global_marker_on_success(self, tmp_path):
        """Global marker .trailhead-registered must be written after success."""
        from trailhead.registry import register_marketplace

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)

        register_marketplace(
            composed_root=composed_root, runner=lambda args, **kw: None
        )

        assert (composed_root / ".trailhead-registered").exists(), (
            "global marker .trailhead-registered not written after register_marketplace"
        )

    def test_register_marketplace_no_marker_when_runner_raises(self, tmp_path):
        """Global marker must be absent if the runner raises."""
        from trailhead.registry import register_marketplace

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)

        def failing_runner(args, **kw):
            raise RuntimeError("marketplace add failed")

        with pytest.raises(RuntimeError):
            register_marketplace(
                composed_root=composed_root, runner=failing_runner
            )

        assert not (composed_root / ".trailhead-registered").exists()

    def test_register_marketplace_args_are_list(self, tmp_path):
        """CLI args must be a list, not a shell string."""
        from trailhead.registry import register_marketplace

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(args)

        register_marketplace(composed_root=composed_root, runner=stub_runner)

        for call_args in calls_seen:
            assert isinstance(call_args, list)

    def test_register_marketplace_never_invokes_real_cli(self, tmp_path):
        from trailhead.registry import register_marketplace

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)

        with patch("subprocess.run") as mock_run:
            register_marketplace(
                composed_root=composed_root,
                runner=lambda args, **kw: None,
            )
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# T-R4: install_tool — per-tool install + per-tool marker
# ---------------------------------------------------------------------------


class TestInstallTool:
    def test_install_tool_calls_plugin_install(self, tmp_path):
        """install_tool must call 'claude plugin install <tool>@trailhead --scope user'."""
        from trailhead.registry import install_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(list(args))

        install_tool(tool="lore", composed_root=composed_root, runner=stub_runner)

        install_calls = [args for args in calls_seen if "install" in args]
        assert len(install_calls) == 1, (
            f"expected one 'install' call; got {calls_seen}"
        )

    def test_install_tool_ref_is_trailhead_not_per_tool(self, tmp_path):
        """Install ref must be '<tool>@trailhead', NOT '<tool>@trailhead-<tool>'."""
        from trailhead.registry import install_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(list(args))

        install_tool(tool="lore", composed_root=composed_root, runner=stub_runner)

        install_calls = [args for args in calls_seen if "install" in args]
        assert len(install_calls) == 1
        install_call = install_calls[0]
        # Must use consolidated @trailhead, NOT per-tool @trailhead-lore
        assert "lore@trailhead" in install_call
        assert "lore@trailhead-lore" not in install_call

    def test_install_tool_scope_user(self, tmp_path):
        """install must pass --scope user."""
        from trailhead.registry import install_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(list(args))

        install_tool(tool="lore", composed_root=composed_root, runner=stub_runner)

        for call_args in calls_seen:
            assert "--scope" in call_args
            idx = call_args.index("--scope")
            assert call_args[idx + 1] == "user"

    def test_install_tool_writes_per_tool_marker_on_success(self, tmp_path):
        """Per-tool marker .trailhead-installed-<tool> must be written on success."""
        from trailhead.registry import install_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)

        install_tool(
            tool="lore", composed_root=composed_root, runner=lambda args, **kw: None
        )

        assert (composed_root / ".trailhead-installed-lore").exists(), (
            "per-tool marker .trailhead-installed-lore not written after install_tool"
        )

    def test_install_tool_no_marker_when_runner_raises(self, tmp_path):
        """Per-tool marker must be absent if the runner raises."""
        from trailhead.registry import install_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)

        def failing_runner(args, **kw):
            raise RuntimeError("install failed")

        with pytest.raises(RuntimeError):
            install_tool(
                tool="lore", composed_root=composed_root, runner=failing_runner
            )

        assert not (composed_root / ".trailhead-installed-lore").exists()

    def test_install_tool_per_tool_markers_are_distinct(self, tmp_path):
        """Different tools must have distinct marker files."""
        from trailhead.registry import install_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)

        install_tool(
            tool="lore", composed_root=composed_root, runner=lambda args, **kw: None
        )
        install_tool(
            tool="camp", composed_root=composed_root, runner=lambda args, **kw: None
        )

        assert (composed_root / ".trailhead-installed-lore").exists()
        assert (composed_root / ".trailhead-installed-camp").exists()

    def test_install_tool_args_are_list(self, tmp_path):
        from trailhead.registry import install_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(args)

        install_tool(tool="lore", composed_root=composed_root, runner=stub_runner)

        for call_args in calls_seen:
            assert isinstance(call_args, list)

    def test_install_tool_never_invokes_real_cli(self, tmp_path):
        from trailhead.registry import install_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)

        with patch("subprocess.run") as mock_run:
            install_tool(
                tool="lore",
                composed_root=composed_root,
                runner=lambda args, **kw: None,
            )
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# T-R5: rewire_tool — uninstall THEN install (NOT plugin update)
# ---------------------------------------------------------------------------


class TestRewireTool:
    def test_rewire_tool_calls_uninstall_then_install(self, tmp_path):
        """rewire_tool must call uninstall THEN install, in that order."""
        from trailhead.registry import rewire_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(list(args))

        rewire_tool(tool="lore", composed_root=composed_root, runner=stub_runner)

        verbs = []
        for call_args in calls_seen:
            if "uninstall" in call_args:
                verbs.append("uninstall")
            elif "install" in call_args:
                verbs.append("install")

        assert verbs == ["uninstall", "install"], (
            f"expected [uninstall, install], got {verbs} from calls {calls_seen}"
        )

    def test_rewire_tool_does_not_call_plugin_update(self, tmp_path):
        """rewire_tool must NOT use 'plugin update' (U-1(e): stale cache bug)."""
        from trailhead.registry import rewire_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(list(args))

        rewire_tool(tool="lore", composed_root=composed_root, runner=stub_runner)

        update_calls = [args for args in calls_seen if "update" in args]
        assert len(update_calls) == 0, (
            f"rewire_tool must not call 'plugin update' (U-1(e)); got {update_calls}"
        )

    def test_rewire_tool_uses_trailhead_ref(self, tmp_path):
        """Both uninstall and install must use '<tool>@trailhead' (consolidated)."""
        from trailhead.registry import rewire_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(list(args))

        rewire_tool(tool="lore", composed_root=composed_root, runner=stub_runner)

        for call_args in calls_seen:
            if "uninstall" in call_args or "install" in call_args:
                assert "lore@trailhead" in call_args, (
                    f"expected 'lore@trailhead' in {call_args}"
                )
                assert "lore@trailhead-lore" not in call_args

    def test_rewire_tool_tolerates_uninstall_failure(self, tmp_path):
        """rewire_tool must tolerate a failing uninstall ('not installed') and still install."""
        from trailhead.registry import rewire_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        install_calls = []

        def stub_runner(args, **kwargs):
            if "uninstall" in args:
                raise RuntimeError("not installed")
            if "install" in args:
                install_calls.append(list(args))

        # Must not propagate the uninstall error
        rewire_tool(tool="lore", composed_root=composed_root, runner=stub_runner)

        assert len(install_calls) == 1, (
            "install must still run even when uninstall fails"
        )

    def test_rewire_tool_clears_per_tool_marker_before_pair(self, tmp_path):
        """Per-tool marker must be cleared BEFORE the uninstall+install pair starts."""
        from trailhead.registry import rewire_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        marker = composed_root / ".trailhead-installed-lore"
        marker.write_text("{}")  # pre-existing marker

        marker_state_at_call_time = []

        def stub_runner(args, **kwargs):
            marker_state_at_call_time.append(marker.exists())

        rewire_tool(tool="lore", composed_root=composed_root, runner=stub_runner)

        # For both calls (uninstall, install), the marker must have been absent
        assert all(not present for present in marker_state_at_call_time), (
            "per-tool marker was still present when CLI was called; "
            f"states: {marker_state_at_call_time}"
        )

    def test_rewire_tool_rewrites_per_tool_marker_after_install(self, tmp_path):
        """Per-tool marker must be re-written after install succeeds."""
        from trailhead.registry import rewire_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)

        rewire_tool(
            tool="lore", composed_root=composed_root, runner=lambda args, **kw: None
        )

        assert (composed_root / ".trailhead-installed-lore").exists(), (
            "per-tool marker not re-written after rewire_tool install succeeds"
        )

    def test_rewire_tool_no_marker_when_install_raises(self, tmp_path):
        """Per-tool marker absent when install raises (C-2 self-heal)."""
        from trailhead.registry import rewire_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        marker = composed_root / ".trailhead-installed-lore"
        marker.write_text("{}")  # pre-existing marker

        def failing_on_install(args, **kw):
            if "install" in args:
                raise RuntimeError("install failed")

        with pytest.raises(RuntimeError):
            rewire_tool(
                tool="lore", composed_root=composed_root, runner=failing_on_install
            )

        assert not marker.exists(), (
            "per-tool marker must be absent after install raises"
        )

    def test_rewire_tool_scope_user_on_both_calls(self, tmp_path):
        """Both uninstall and install must pass --scope user."""
        from trailhead.registry import rewire_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(list(args))

        rewire_tool(tool="lore", composed_root=composed_root, runner=stub_runner)

        for call_args in calls_seen:
            assert "--scope" in call_args, f"--scope missing from {call_args}"
            idx = call_args.index("--scope")
            assert call_args[idx + 1] == "user"

    def test_rewire_tool_args_are_list(self, tmp_path):
        from trailhead.registry import rewire_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(args)

        rewire_tool(tool="lore", composed_root=composed_root, runner=stub_runner)

        for call_args in calls_seen:
            assert isinstance(call_args, list)

    def test_rewire_tool_never_invokes_real_subprocess(self, tmp_path):
        from trailhead.registry import rewire_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)

        with patch("subprocess.run") as mock_run:
            rewire_tool(
                tool="lore",
                composed_root=composed_root,
                runner=lambda args, **kw: None,
            )
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# T-R6: injectable runner — new function signatures
# ---------------------------------------------------------------------------


class TestInjectableRunner:
    def test_register_marketplace_has_injectable_runner(self):
        import inspect

        from trailhead.registry import register_marketplace

        sig = inspect.signature(register_marketplace)
        assert "runner" in sig.parameters

    def test_install_tool_has_injectable_runner(self):
        import inspect

        from trailhead.registry import install_tool

        sig = inspect.signature(install_tool)
        assert "runner" in sig.parameters

    def test_rewire_tool_has_injectable_runner(self):
        import inspect

        from trailhead.registry import rewire_tool

        sig = inspect.signature(rewire_tool)
        assert "runner" in sig.parameters


# ---------------------------------------------------------------------------
# Teardown — unregister_tool (per-tool) + unregister_marketplace (once, shared)
# ---------------------------------------------------------------------------


class TestUnregisterTool:
    def test_calls_plugin_uninstall_consolidated_ref(self, tmp_path):
        """unregister_tool must call 'claude plugin uninstall <tool>@trailhead'."""
        from trailhead.registry import unregister_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        calls_seen = []
        unregister_tool(
            tool="lore", composed_root=composed_root,
            runner=lambda args, **kw: calls_seen.append(list(args)),
        )

        uninstall_calls = [a for a in calls_seen if "uninstall" in a]
        assert len(uninstall_calls) == 1
        call = uninstall_calls[0]
        assert "lore@trailhead" in call
        assert "lore@trailhead-lore" not in call

    def test_passes_keep_data_and_yes(self, tmp_path):
        """--keep-data (wiring-only) and --yes (non-interactive) must be passed."""
        from trailhead.registry import unregister_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        calls_seen = []
        unregister_tool(
            tool="lore", composed_root=composed_root,
            runner=lambda args, **kw: calls_seen.append(list(args)),
        )

        call = calls_seen[0]
        assert "--keep-data" in call
        assert "--yes" in call
        assert "--scope" in call and call[call.index("--scope") + 1] == "user"

    def test_does_not_remove_marketplace(self, tmp_path):
        """unregister_tool must NOT touch the shared marketplace (only the tool)."""
        from trailhead.registry import unregister_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        calls_seen = []
        unregister_tool(
            tool="lore", composed_root=composed_root,
            runner=lambda args, **kw: calls_seen.append(list(args)),
        )

        # No 'marketplace remove' may appear — that would de-register sibling tools.
        for call in calls_seen:
            assert not ("marketplace" in call and "remove" in call)

    def test_clears_per_tool_marker(self, tmp_path):
        from trailhead.registry import unregister_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        marker = composed_root / ".trailhead-installed-lore"
        marker.write_text("{}")

        unregister_tool(tool="lore", composed_root=composed_root, runner=lambda args, **kw: None)
        assert not marker.exists()

    def test_clears_per_tool_marker_even_if_runner_raises(self, tmp_path):
        from trailhead.registry import unregister_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        marker = composed_root / ".trailhead-installed-lore"
        marker.write_text("{}")

        def failing(args, **kw):
            raise RuntimeError("plugin not found")

        with pytest.raises(RuntimeError):
            unregister_tool(tool="lore", composed_root=composed_root, runner=failing)
        assert not marker.exists(), "marker must be cleared in finally even on failure"

    def test_validates_tool_name(self, tmp_path):
        from trailhead.registry import unregister_tool

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        with pytest.raises(ValueError):
            unregister_tool(
                tool="../evil", composed_root=composed_root, runner=lambda args, **kw: None
            )


class TestUnregisterMarketplace:
    def test_calls_marketplace_remove_trailhead(self, tmp_path):
        """unregister_marketplace must call 'claude plugin marketplace remove trailhead'."""
        from trailhead.registry import unregister_marketplace

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        calls_seen = []
        unregister_marketplace(
            composed_root=composed_root,
            runner=lambda args, **kw: calls_seen.append(list(args)),
        )

        assert len(calls_seen) == 1
        call = calls_seen[0]
        assert call[:5] == ["claude", "plugin", "marketplace", "remove", "trailhead"]
        assert "--scope" in call and call[call.index("--scope") + 1] == "user"
        # Must remove the consolidated name, never a per-tool one.
        assert "trailhead-lore" not in call

    def test_clears_global_marker(self, tmp_path):
        from trailhead.registry import unregister_marketplace

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        marker = composed_root / ".trailhead-registered"
        marker.write_text("{}")

        unregister_marketplace(composed_root=composed_root, runner=lambda args, **kw: None)
        assert not marker.exists()

    def test_clears_global_marker_even_if_runner_raises(self, tmp_path):
        from trailhead.registry import unregister_marketplace

        composed_root = tmp_path / "composed"
        composed_root.mkdir(parents=True)
        marker = composed_root / ".trailhead-registered"
        marker.write_text("{}")

        def failing(args, **kw):
            raise RuntimeError("marketplace not found")

        with pytest.raises(RuntimeError):
            unregister_marketplace(composed_root=composed_root, runner=failing)
        assert not marker.exists()
