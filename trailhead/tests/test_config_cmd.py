"""Tests for trailhead config subcommand (Slice 5).

TDD: tests written BEFORE implementation. Each test must fail before
implementation exists, then pass after.

Test contract (plan §Slice 5 + amendments R-2, D-7, A-9):
  - config registry [<value>]: read current registry (stdout) / set and persist.
  - config path_integration [on|off]: toggle + trigger PATH install/remove.
    off → remove_path_integration called; on → install_path_integration called.
  - config capabilities <tool> <cap> on|off: re-wires the tool, persists ONLY on
    success (R-2 ordering — wire first, then persist; on failure config unchanged).
  - config active-group [<name>]: read/write camp's active group config surface.
  - Reading with no value → stdout, errors → stderr, --json for machine reads.
  - R-2: failed re-wire → config unchanged + named error.

Hermeticity:
  - wire and pathint are always stubbed.
  - tmp_path for config/state dirs.
  - No real ~/.claude/, real state_dir, or real `camp doctor` calls.
"""

import io
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hermetic_env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "TRAILHEAD_CONFIG_DIR": str(tmp_path / "config"),
        "TRAILHEAD_STATE_DIR": str(tmp_path / "state"),
    }


def _save_config(env: dict[str, str], **kwargs):
    """Save a config with given fields (others defaulted)."""
    from trailhead.config import TrailheadConfig, save_config
    cfg = TrailheadConfig(**kwargs)
    save_config(cfg, env=env)
    return cfg


def _load_config(env: dict[str, str]):
    from trailhead.config import load_config
    return load_config(env=env)


def _run_config(
    args: list[str],
    *,
    env: dict[str, str],
    wire_side_effect=None,
    wire_return=None,
    pathint_side_effect=None,
    pathint_return=None,
    remove_pathint_side_effect=None,
):
    """Run run_config() with stubbed wire + pathint.

    Returns (exit_code, stdout_str, stderr_str).
    """
    from trailhead import config_cmd as config_cmd_mod

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    try:
        sys.stdout = stdout_buf
        sys.stderr = stderr_buf

        with patch("trailhead.config_cmd.wire") as mock_wire, \
             patch("trailhead.config_cmd.install_path_integration") as mock_pathint, \
             patch("trailhead.config_cmd.remove_path_integration") as mock_remove_pathint:

            # Defaults
            mock_wire.return_value = None
            mock_pathint.return_value = MagicMock(shim_dir=Path("/tmp"), rc_path=None, skip_message=None)
            mock_remove_pathint.return_value = None

            if wire_side_effect is not None:
                mock_wire.side_effect = wire_side_effect
            elif wire_return is not None:
                mock_wire.return_value = wire_return

            if pathint_side_effect is not None:
                mock_pathint.side_effect = pathint_side_effect
            elif pathint_return is not None:
                mock_pathint.return_value = pathint_return

            if remove_pathint_side_effect is not None:
                mock_remove_pathint.side_effect = remove_pathint_side_effect

            try:
                exit_code = config_cmd_mod.run_config(args, env=env)
            except SystemExit as e:
                exit_code = e.code if isinstance(e.code, int) else 0

    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()


# ---------------------------------------------------------------------------
# T-C1: config registry — read and set
# ---------------------------------------------------------------------------


class TestConfigRegistry:
    def test_read_registry_prints_to_stdout(self, tmp_path):
        """config registry with no value prints current registry to stdout."""
        env = _hermetic_env(tmp_path)
        _save_config(env, registry="github.com/my-org")
        code, out, err = _run_config(["registry"], env=env)
        assert code == 0
        assert "github.com/my-org" in out

    def test_read_registry_default_value(self, tmp_path):
        """config registry with no value and no config → prints default."""
        env = _hermetic_env(tmp_path)
        code, out, err = _run_config(["registry"], env=env)
        assert code == 0
        assert len(out.strip()) > 0

    def test_set_registry_persists(self, tmp_path):
        """config registry <value> persists the new registry to config."""
        env = _hermetic_env(tmp_path)
        code, out, err = _run_config(
            ["registry", "github.example-corp.internal/trailhead"],
            env=env,
        )
        assert code == 0
        cfg = _load_config(env)
        assert cfg.registry == "github.example-corp.internal/trailhead"

    def test_set_registry_no_error_to_stderr(self, tmp_path):
        """config registry <value> success → no error output."""
        env = _hermetic_env(tmp_path)
        code, out, err = _run_config(
            ["registry", "github.example-corp.internal/trailhead"],
            env=env,
        )
        assert err.strip() == ""


# ---------------------------------------------------------------------------
# T-C2: config path_integration — toggle on/off
# ---------------------------------------------------------------------------


class TestConfigPathIntegration:
    def test_path_integration_off_removes_path(self, tmp_path):
        """config path_integration off → remove_path_integration is called."""
        env = _hermetic_env(tmp_path)
        _save_config(env, path_integration=True)

        remove_called = []

        def track_remove(*args, **kwargs):
            remove_called.append(True)

        code, out, err = _run_config(
            ["path_integration", "off"],
            env=env,
            remove_pathint_side_effect=track_remove,
        )
        assert code == 0
        assert remove_called, "remove_path_integration must be called when toggling off"

    def test_path_integration_off_persists_config(self, tmp_path):
        """config path_integration off → config.path_integration becomes False."""
        env = _hermetic_env(tmp_path)
        _save_config(env, path_integration=True)

        code, out, err = _run_config(["path_integration", "off"], env=env)
        assert code == 0
        cfg = _load_config(env)
        assert cfg.path_integration is False

    def test_path_integration_on_installs_path(self, tmp_path):
        """config path_integration on → install_path_integration is called."""
        env = _hermetic_env(tmp_path)
        _save_config(env, path_integration=False)

        install_called = []

        def track_install(*args, **kwargs):
            install_called.append(True)
            return MagicMock(shim_dir=Path("/tmp"), rc_path=None, skip_message=None)

        code, out, err = _run_config(
            ["path_integration", "on"],
            env=env,
            pathint_side_effect=track_install,
        )
        assert code == 0
        assert install_called, "install_path_integration must be called when toggling on"

    def test_path_integration_on_persists_config(self, tmp_path):
        """config path_integration on → config.path_integration becomes True."""
        env = _hermetic_env(tmp_path)
        _save_config(env, path_integration=False)

        code, out, err = _run_config(["path_integration", "on"], env=env)
        assert code == 0
        cfg = _load_config(env)
        assert cfg.path_integration is True

    def test_path_integration_read_prints_value(self, tmp_path):
        """config path_integration with no arg prints current value."""
        env = _hermetic_env(tmp_path)
        _save_config(env, path_integration=True)

        code, out, err = _run_config(["path_integration"], env=env)
        assert code == 0
        assert "on" in out.lower() or "true" in out.lower()


# ---------------------------------------------------------------------------
# T-C3: config capabilities — runtime gate + R-2 ordering
# ---------------------------------------------------------------------------


class TestConfigCapabilities:
    def test_capabilities_off_triggers_rewire(self, tmp_path):
        """config capabilities lore recall off → wire() is called for lore."""
        env = _hermetic_env(tmp_path)
        _save_config(env, capabilities={"lore": ["capture", "recall", "sessions"]})

        wire_calls = []

        def track_wire(selection, **kwargs):
            wire_calls.append(selection)

        code, out, err = _run_config(
            ["capabilities", "lore", "recall", "off"],
            env=env,
            wire_side_effect=track_wire,
        )
        assert code == 0
        assert wire_calls, "wire() must be called when toggling a capability off"
        # The wire call should have lore WITHOUT recall
        lore_caps = wire_calls[0].get("lore", set())
        assert "recall" not in lore_caps

    def test_capabilities_off_persists_after_successful_rewire(self, tmp_path):
        """config capabilities off → persists only after successful wire (R-2)."""
        env = _hermetic_env(tmp_path)
        _save_config(env, capabilities={"lore": ["capture", "recall", "sessions"]})

        code, out, err = _run_config(
            ["capabilities", "lore", "recall", "off"],
            env=env,
        )
        assert code == 0
        cfg = _load_config(env)
        assert "recall" not in cfg.capabilities.get("lore", [])

    def test_capabilities_off_wire_failure_config_unchanged(self, tmp_path):
        """R-2: if wire fails, config is NOT updated (remains unchanged)."""
        env = _hermetic_env(tmp_path)
        _save_config(env, capabilities={"lore": ["capture", "recall", "sessions"]})

        from trailhead.wire import WireError

        code, out, err = _run_config(
            ["capabilities", "lore", "recall", "off"],
            env=env,
            wire_side_effect=WireError(tool="lore", stage="compose", cause=Exception("boom")),
        )
        assert code != 0
        # Config must be UNCHANGED — recall still in list
        cfg = _load_config(env)
        assert "recall" in cfg.capabilities.get("lore", [])

    def test_capabilities_off_wire_failure_named_error(self, tmp_path):
        """R-2: wire failure → named error on stderr."""
        env = _hermetic_env(tmp_path)
        _save_config(env, capabilities={"lore": ["capture", "recall", "sessions"]})

        from trailhead.wire import WireError

        code, out, err = _run_config(
            ["capabilities", "lore", "recall", "off"],
            env=env,
            wire_side_effect=WireError(tool="lore", stage="compose", cause=Exception("boom")),
        )
        assert code != 0
        assert len(err.strip()) > 0

    def test_capabilities_on_triggers_rewire(self, tmp_path):
        """config capabilities lore recall on → wire() called with recall included."""
        env = _hermetic_env(tmp_path)
        _save_config(env, capabilities={"lore": ["capture", "sessions"]})

        wire_calls = []

        def track_wire(selection, **kwargs):
            wire_calls.append(selection)

        code, out, err = _run_config(
            ["capabilities", "lore", "recall", "on"],
            env=env,
            wire_side_effect=track_wire,
        )
        assert code == 0
        lore_caps = wire_calls[0].get("lore", set())
        assert "recall" in lore_caps

    def test_capabilities_on_persists_after_successful_rewire(self, tmp_path):
        """config capabilities on → persisted after successful wire."""
        env = _hermetic_env(tmp_path)
        _save_config(env, capabilities={"lore": ["capture", "sessions"]})

        code, out, err = _run_config(
            ["capabilities", "lore", "recall", "on"],
            env=env,
        )
        assert code == 0
        cfg = _load_config(env)
        assert "recall" in cfg.capabilities.get("lore", [])

    def test_capabilities_read_prints_current(self, tmp_path):
        """config capabilities with no toggle argument prints current caps."""
        env = _hermetic_env(tmp_path)
        _save_config(env, capabilities={"lore": ["capture", "recall"]})

        code, out, err = _run_config(["capabilities"], env=env)
        assert code == 0
        assert "lore" in out.lower() or "capture" in out.lower() or "recall" in out.lower()


# ---------------------------------------------------------------------------
# T-C4: config active-group — read camp's group config
# ---------------------------------------------------------------------------


class TestConfigActiveGroup:
    def test_active_group_read_no_groups_prints_info(self, tmp_path):
        """config active-group with no groups configured → informative output, no crash."""
        env = _hermetic_env(tmp_path)
        code, out, err = _run_config(["active-group"], env=env)
        assert code == 0
        # Should print something about no groups or current group
        assert len(out.strip()) > 0 or len(err.strip()) > 0

    def test_active_group_with_name_reports(self, tmp_path):
        """config active-group <name> sets or looks up a group by name."""
        env = _hermetic_env(tmp_path)
        # No real camp config needed — just check it doesn't crash hard
        code, out, err = _run_config(["active-group", "my-group"], env=env)
        # Should not crash; may report group not found
        assert isinstance(code, int)


# ---------------------------------------------------------------------------
# T-C5: A-9 hygiene — errors to stderr, values to stdout
# ---------------------------------------------------------------------------


class TestA9Hygiene:
    def test_registry_read_value_to_stdout(self, tmp_path):
        """Values go to stdout (A-9)."""
        env = _hermetic_env(tmp_path)
        code, out, err = _run_config(["registry"], env=env)
        assert code == 0
        assert len(out.strip()) > 0

    def test_failure_error_to_stderr(self, tmp_path):
        """Errors go to stderr (A-9)."""
        env = _hermetic_env(tmp_path)
        _save_config(env, capabilities={"lore": ["recall"]})

        from trailhead.wire import WireError

        code, out, err = _run_config(
            ["capabilities", "lore", "recall", "off"],
            env=env,
            wire_side_effect=WireError(tool="lore", stage="compose", cause=Exception("fail")),
        )
        assert code != 0
        assert len(err.strip()) > 0

    def test_unknown_subcommand_error_to_stderr(self, tmp_path):
        """Unknown config subcommand → error to stderr."""
        env = _hermetic_env(tmp_path)
        code, out, err = _run_config(["bogus-subcommand"], env=env)
        assert code != 0
        assert len(err.strip()) > 0
