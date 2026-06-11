"""Tests for trailhead/registry.py — harness-registration concern.

TDD: written BEFORE implementation. All must fail first, then pass.

Contract (B-3 HERMETICITY):
  - registry.generate_marketplace_json writes marketplace.json at
    <mkt_root>/.claude-plugin/marketplace.json with exact Shape A.
  - registry.register shells the harness CLI for marketplace add + install.
  - registry.rewire shells the harness CLI for plugin update.
  - The harness-CLI invocation is injectable/patchable — tests NEVER invoke
    the real `claude plugin` CLI.
  - All paths resolve through TRAILHEAD_STATE_DIR env override (hermetic).
  - Never writes to ~/.claude/plugins/ — only writes marketplace.json under
    the mkt_root and shells the CLI (which the test stubs).
"""

import json
import os
from pathlib import Path
from unittest.mock import call, patch

import pytest


# ---------------------------------------------------------------------------
# Helper: build a minimal environment dict that redirects state_dir
# ---------------------------------------------------------------------------


def _env(tmp_path: Path) -> dict[str, str]:
    """Return an env dict that redirects TRAILHEAD_STATE_DIR to tmp_path."""
    return {**os.environ, "TRAILHEAD_STATE_DIR": str(tmp_path)}


# ---------------------------------------------------------------------------
# T-R1: generate_marketplace_json writes Shape-A marketplace.json
# ---------------------------------------------------------------------------


class TestGenerateMarketplaceJson:
    def test_marketplace_json_written(self, tmp_path):
        from trailhead.registry import generate_marketplace_json

        mkt_root = tmp_path / "composed" / "lore"
        mkt_root.mkdir(parents=True)
        generate_marketplace_json(tool="lore", mkt_root=mkt_root)
        mkt_json = mkt_root / ".claude-plugin" / "marketplace.json"
        assert mkt_json.exists()

    def test_marketplace_json_shape_a(self, tmp_path):
        """marketplace.json must match Shape A exactly."""
        from trailhead.registry import generate_marketplace_json

        mkt_root = tmp_path / "composed" / "lore"
        mkt_root.mkdir(parents=True)
        generate_marketplace_json(tool="lore", mkt_root=mkt_root)
        mkt_json = mkt_root / ".claude-plugin" / "marketplace.json"
        data = json.loads(mkt_json.read_text())

        assert data["name"] == "trailhead-lore"
        assert data["owner"] == {"name": "trailhead"}
        assert "description" in data
        assert isinstance(data["description"], str)
        assert len(data["plugins"]) == 1
        plugin = data["plugins"][0]
        assert plugin["name"] == "lore"
        assert plugin["source"] == "./plugins/lore"
        assert "description" in plugin
        assert isinstance(plugin["description"], str)

    def test_marketplace_json_parses_as_json(self, tmp_path):
        from trailhead.registry import generate_marketplace_json

        mkt_root = tmp_path / "composed" / "forge"
        mkt_root.mkdir(parents=True)
        generate_marketplace_json(tool="forge", mkt_root=mkt_root)
        mkt_json = mkt_root / ".claude-plugin" / "marketplace.json"
        data = json.loads(mkt_json.read_text())
        assert isinstance(data, dict)

    def test_marketplace_json_tool_name_in_name_field(self, tmp_path):
        """marketplace name must be 'trailhead-<tool>'."""
        from trailhead.registry import generate_marketplace_json

        for tool in ("lore", "camp", "forge"):
            mkt_root = tmp_path / tool
            mkt_root.mkdir(parents=True)
            generate_marketplace_json(tool=tool, mkt_root=mkt_root)
            data = json.loads(
                (mkt_root / ".claude-plugin" / "marketplace.json").read_text()
            )
            assert data["name"] == f"trailhead-{tool}"

    def test_marketplace_json_plugin_source_points_at_plugins_subdir(self, tmp_path):
        """The plugin source must be './plugins/<tool>' (relative)."""
        from trailhead.registry import generate_marketplace_json

        for tool in ("lore", "camp", "forge"):
            mkt_root = tmp_path / tool
            mkt_root.mkdir(parents=True)
            generate_marketplace_json(tool=tool, mkt_root=mkt_root)
            data = json.loads(
                (mkt_root / ".claude-plugin" / "marketplace.json").read_text()
            )
            assert data["plugins"][0]["source"] == f"./plugins/{tool}"

    def test_claude_plugin_dir_created_automatically(self, tmp_path):
        """generate_marketplace_json must create .claude-plugin/ if absent."""
        from trailhead.registry import generate_marketplace_json

        mkt_root = tmp_path / "composed" / "lore"
        mkt_root.mkdir(parents=True)
        claude_dir = mkt_root / ".claude-plugin"
        assert not claude_dir.exists()
        generate_marketplace_json(tool="lore", mkt_root=mkt_root)
        assert claude_dir.exists()


# ---------------------------------------------------------------------------
# T-R2: register — invokes the CLI with expected args (stubbed runner)
# ---------------------------------------------------------------------------


class TestRegisterInvokesCliArgs:
    def test_register_calls_marketplace_add(self, tmp_path):
        """register must call 'claude plugin marketplace add --scope user <mkt_root>'."""
        from trailhead.registry import register

        mkt_root = tmp_path / "composed" / "lore"
        mkt_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(args)

        register(tool="lore", mkt_root=mkt_root, runner=stub_runner)

        add_calls = [
            args for args in calls_seen
            if "marketplace" in args and "add" in args
        ]
        assert len(add_calls) == 1, (
            f"expected one 'marketplace add' call; got {calls_seen}"
        )
        assert str(mkt_root) in add_calls[0]

    def test_register_calls_plugin_install(self, tmp_path):
        """register must call 'claude plugin install <tool>@trailhead-<tool> --scope user'."""
        from trailhead.registry import register

        mkt_root = tmp_path / "composed" / "lore"
        mkt_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(args)

        register(tool="lore", mkt_root=mkt_root, runner=stub_runner)

        install_calls = [
            args for args in calls_seen
            if "install" in args
        ]
        assert len(install_calls) == 1, (
            f"expected one 'install' call; got {calls_seen}"
        )
        install_call = install_calls[0]
        assert "lore@trailhead-lore" in install_call

    def test_register_scope_user(self, tmp_path):
        """Both CLI calls must pass --scope user."""
        from trailhead.registry import register

        mkt_root = tmp_path / "composed" / "lore"
        mkt_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(list(args))

        register(tool="lore", mkt_root=mkt_root, runner=stub_runner)

        for call_args in calls_seen:
            assert "--scope" in call_args, (
                f"--scope missing from call: {call_args}"
            )
            idx = call_args.index("--scope")
            assert call_args[idx + 1] == "user", (
                f"expected --scope user, got {call_args[idx+1]}"
            )

    def test_register_never_invokes_real_cli(self, tmp_path):
        """register with a stub runner must not touch subprocess.run."""
        from trailhead.registry import register

        mkt_root = tmp_path / "composed" / "lore"
        mkt_root.mkdir(parents=True)

        with patch("subprocess.run") as mock_run:
            register(
                tool="lore",
                mkt_root=mkt_root,
                runner=lambda args, **kw: None,  # stub always wins
            )
            mock_run.assert_not_called()

    def test_register_args_are_list_not_string(self, tmp_path):
        """CLI args must be a list, never a shell string (injection safety)."""
        from trailhead.registry import register

        mkt_root = tmp_path / "composed" / "forge"
        mkt_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(args)

        register(tool="forge", mkt_root=mkt_root, runner=stub_runner)

        for call_args in calls_seen:
            assert isinstance(call_args, list), (
                f"CLI args must be a list, got {type(call_args)}: {call_args!r}"
            )


# ---------------------------------------------------------------------------
# T-R3: rewire — invokes plugin update
# ---------------------------------------------------------------------------


class TestRewireInvokesUpdate:
    def test_rewire_calls_plugin_update(self, tmp_path):
        """rewire must call 'claude plugin update <tool>@trailhead-<tool>'."""
        from trailhead.registry import rewire

        mkt_root = tmp_path / "composed" / "lore"
        mkt_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(list(args))

        rewire(tool="lore", mkt_root=mkt_root, runner=stub_runner)

        update_calls = [
            args for args in calls_seen
            if "update" in args
        ]
        assert len(update_calls) == 1, (
            f"expected one 'update' call; got {calls_seen}"
        )
        update_args = update_calls[0]
        assert "lore@trailhead-lore" in update_args

    def test_rewire_args_are_list(self, tmp_path):
        from trailhead.registry import rewire

        mkt_root = tmp_path / "composed" / "lore"
        mkt_root.mkdir(parents=True)
        calls_seen = []

        def stub_runner(args, **kwargs):
            calls_seen.append(args)

        rewire(tool="lore", mkt_root=mkt_root, runner=stub_runner)

        for call_args in calls_seen:
            assert isinstance(call_args, list)

    def test_rewire_never_invokes_real_subprocess(self, tmp_path):
        from trailhead.registry import rewire

        mkt_root = tmp_path / "composed" / "lore"
        mkt_root.mkdir(parents=True)

        with patch("subprocess.run") as mock_run:
            rewire(
                tool="lore",
                mkt_root=mkt_root,
                runner=lambda args, **kw: None,
            )
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# T-R4: default runner is subprocess.run (but tests never exercise it)
# ---------------------------------------------------------------------------


class TestDefaultRunnerShape:
    def test_register_has_injectable_runner(self, tmp_path):
        """register accepts a runner= kwarg (injectable/patchable)."""
        import inspect

        from trailhead.registry import register

        sig = inspect.signature(register)
        assert "runner" in sig.parameters, (
            "register must accept a runner= kwarg for test injection"
        )

    def test_rewire_has_injectable_runner(self, tmp_path):
        import inspect

        from trailhead.registry import rewire

        sig = inspect.signature(rewire)
        assert "runner" in sig.parameters, (
            "rewire must accept a runner= kwarg for test injection"
        )
