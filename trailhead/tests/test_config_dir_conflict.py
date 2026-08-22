"""Refusal when the trailhead seam and Claude Code's own relocation disagree.

`TRAILHEAD_CLAUDE_DIR` is a trailhead-only test seam; `CLAUDE_CONFIG_DIR` is
Claude Code's own relocation variable. When both are set to *different*
directories there is no way to honour both: markers and the CLI's own plugin
state must land together, and the trust key the launched session reads lands
beside the config dir Claude Code itself resolves. Picking either one silently
produces a session that launches, is trusted, and has none of trailhead's
plugins — so the harness refuses instead.
"""

from __future__ import annotations

import pytest

from trailhead.harness.base import HarnessError
from trailhead.harness.claude_code import ClaudeCodeHarness

from .conftest import capturing_runner


def _conflicting(tmp_path):
    return {
        "TRAILHEAD_CLAUDE_DIR": str(tmp_path / "seam"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "real"),
        "HOME": str(tmp_path),
    }


class TestTheRefusalMessage:
    def test_it_names_both_values_and_what_to_do(self, tmp_path):
        env = _conflicting(tmp_path)
        with pytest.raises(HarnessError) as exc_info:
            ClaudeCodeHarness().is_registered(tmp_path / "composed", env=env)
        message = str(exc_info.value)
        assert "TRAILHEAD_CLAUDE_DIR" in message
        assert "CLAUDE_CONFIG_DIR" in message
        assert str(tmp_path / "seam") in message
        assert str(tmp_path / "real") in message
        assert "Unset TRAILHEAD_CLAUDE_DIR" in message


class TestEveryPathThatReachesLiveState:
    """Each entry point that shells the CLI or reports installed state refuses."""

    def test_register_refuses_before_shelling_the_cli(self, tmp_path):
        runner, calls = capturing_runner()
        with pytest.raises(HarnessError):
            ClaudeCodeHarness().register(
                tmp_path / "composed", runner=runner, env=_conflicting(tmp_path)
            )
        assert calls == []

    def test_install_tool_refuses_before_shelling_the_cli(self, tmp_path):
        runner, calls = capturing_runner()
        with pytest.raises(HarnessError):
            ClaudeCodeHarness().install_tool(
                "lore", tmp_path / "composed", runner=runner, env=_conflicting(tmp_path)
            )
        assert calls == []

    def test_rewire_tool_refuses_before_shelling_the_cli(self, tmp_path):
        runner, calls = capturing_runner()
        with pytest.raises(HarnessError):
            ClaudeCodeHarness().rewire_tool(
                "lore", tmp_path / "composed", runner=runner, env=_conflicting(tmp_path)
            )
        assert calls == []

    def test_unregister_tool_refuses_before_shelling_the_cli(self, tmp_path):
        runner, calls = capturing_runner()
        with pytest.raises(HarnessError):
            ClaudeCodeHarness().unregister_tool(
                "lore", tmp_path / "composed", runner=runner, env=_conflicting(tmp_path)
            )
        assert calls == []

    def test_unregister_marketplace_refuses_before_shelling_the_cli(self, tmp_path):
        runner, calls = capturing_runner()
        with pytest.raises(HarnessError):
            ClaudeCodeHarness().unregister_marketplace(
                tmp_path / "composed", runner=runner, env=_conflicting(tmp_path)
            )
        assert calls == []

    @pytest.mark.parametrize("reader", ["is_registered", "installed_tools"])
    def test_the_state_readers_doctor_uses_refuse(self, tmp_path, reader):
        harness = ClaudeCodeHarness()
        with pytest.raises(HarnessError):
            getattr(harness, reader)(tmp_path / "composed", env=_conflicting(tmp_path))

    def test_is_installed_refuses(self, tmp_path):
        with pytest.raises(HarnessError):
            ClaudeCodeHarness().is_installed(
                "lore", tmp_path / "composed", env=_conflicting(tmp_path)
            )

    def test_composed_tree_in_use_elsewhere_refuses(self, tmp_path):
        with pytest.raises(HarnessError):
            ClaudeCodeHarness().composed_tree_in_use_elsewhere(
                tmp_path / "composed", env=_conflicting(tmp_path)
            )

    def test_the_ambient_process_environment_is_checked_too(self, tmp_path, monkeypatch):
        """`env=None` falls back to os.environ — a leaked shell must refuse there too."""
        monkeypatch.setenv("TRAILHEAD_CLAUDE_DIR", str(tmp_path / "seam"))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "real"))
        with pytest.raises(HarnessError):
            ClaudeCodeHarness().is_registered(tmp_path / "composed")


class TestWhatMustNotRefuse:
    def test_equal_values_are_a_consistent_statement_of_intent(self, tmp_path):
        shared = tmp_path / "same"
        env = {
            "TRAILHEAD_CLAUDE_DIR": str(shared),
            "CLAUDE_CONFIG_DIR": str(shared),
            "HOME": str(tmp_path),
        }
        assert ClaudeCodeHarness().is_registered(tmp_path / "composed", env=env) is False

    def test_equal_after_normalisation_does_not_refuse(self, tmp_path):
        shared = tmp_path / "same"
        env = {
            "TRAILHEAD_CLAUDE_DIR": f"{shared}/",
            "CLAUDE_CONFIG_DIR": str(shared),
        }
        assert ClaudeCodeHarness().is_registered(tmp_path / "composed", env=env) is False

    def test_the_seam_alone_is_the_ordinary_test_case(self, tmp_path):
        env = {"TRAILHEAD_CLAUDE_DIR": str(tmp_path / "seam"), "HOME": str(tmp_path)}
        assert ClaudeCodeHarness().is_registered(tmp_path / "composed", env=env) is False

    def test_claude_config_dir_alone_is_the_relocated_account(self, tmp_path):
        env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "real"), "HOME": str(tmp_path)}
        assert ClaudeCodeHarness().is_registered(tmp_path / "composed", env=env) is False

    def test_an_empty_seam_value_is_not_a_conflict(self, tmp_path):
        env = {
            "TRAILHEAD_CLAUDE_DIR": "",
            "CLAUDE_CONFIG_DIR": str(tmp_path / "real"),
            "HOME": str(tmp_path),
        }
        assert ClaudeCodeHarness().is_registered(tmp_path / "composed", env=env) is False
