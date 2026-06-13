"""Tests for trailhead uninstall — run_uninstall end-to-end.

Contract:
  - Discovers wired tools from config capabilities AND from composed/ trees
    that carry a registration marker (union).
  - De-registers each tool from the harness (injected/stubbed — never the real
    `claude plugin` CLI) and deletes its composed tree.
  - Removes PATH integration (stubbed in tests) + the shim dir.
  - Deletes trailhead's config.toml + state bookkeeping.  Keeps user DATA.
  - TTY + no --yes → confirmation prompt; 'n' aborts with nothing changed.
  - Best-effort: a harness de-registration that raises → warning on stderr,
    local teardown still completes, exit 0.
  - --json machine-readable; --quiet suppresses progress.
  - nothing-to-uninstall → exit 0 with a clear message.

Hermeticity:
  All tests use tmp_path for config/state dirs.  unregister and
  remove_path_integration are always stubbed so no test touches the real
  harness or the real shell rc.
"""

import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def _hermetic_env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "TRAILHEAD_CONFIG_DIR": str(tmp_path / "config"),
        "TRAILHEAD_STATE_DIR": str(tmp_path / "state"),
    }


def _make_composed_tool(tmp_path: Path, tool: str, *, registered: bool = True) -> Path:
    """Create a composed/<tool> tree (optionally with a registration marker)."""
    mkt_root = tmp_path / "state" / "composed" / tool
    (mkt_root / "plugins" / tool).mkdir(parents=True, exist_ok=True)
    if registered:
        (mkt_root / ".trailhead-registered").write_text("{}")
    return mkt_root


def _run_uninstall(
    *,
    env: dict[str, str],
    is_tty: bool = False,
    assume_yes: bool = False,
    quiet: bool = False,
    as_json: bool = False,
    stdin_text: str = "",
    unregister_side_effect=None,
):
    """Run run_uninstall with unregister + remove_path_integration stubbed.

    Returns (exit_code, stdout, stderr, unregister_calls).
    """
    from trailhead import uninstall as uninstall_mod

    unregister_calls = []

    def fake_unregister(tool, mkt_root, *, runner=None):
        unregister_calls.append(tool)
        if unregister_side_effect is not None:
            unregister_side_effect(tool)

    old_stdout, old_stderr = sys.stdout, sys.stderr
    out_buf, err_buf = io.StringIO(), io.StringIO()
    try:
        sys.stdout, sys.stderr = out_buf, err_buf
        with patch("trailhead.uninstall.unregister", side_effect=fake_unregister), \
             patch("trailhead.uninstall.remove_path_integration") as mock_rpi, \
             patch("trailhead.uninstall.sys.stdin", io.StringIO(stdin_text)), \
             patch("trailhead.uninstall._is_tty", return_value=is_tty):
            mock_rpi.return_value = None
            try:
                code = uninstall_mod.run_uninstall(
                    env=env, quiet=quiet, as_json=as_json, assume_yes=assume_yes,
                )
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 0
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
    return code, out_buf.getvalue(), err_buf.getvalue(), unregister_calls


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_discovers_from_config_capabilities(self, tmp_path):
        from trailhead.config import TrailheadConfig, save_config

        env = _hermetic_env(tmp_path)
        save_config(TrailheadConfig(capabilities={"lore": ["capture"], "camp": []}), env=env)
        _make_composed_tool(tmp_path, "lore")
        _make_composed_tool(tmp_path, "camp")

        code, out, err, calls = _run_uninstall(env=env, assume_yes=True)
        assert code == 0
        assert set(calls) == {"lore", "camp"}

    def test_discovers_from_composed_markers_without_config(self, tmp_path):
        """No config.toml, but composed trees with markers → still discovered."""
        env = _hermetic_env(tmp_path)
        _make_composed_tool(tmp_path, "lore")
        _make_composed_tool(tmp_path, "camp")
        _make_composed_tool(tmp_path, "craft")

        code, out, err, calls = _run_uninstall(env=env, assume_yes=True)
        assert set(calls) == {"lore", "camp", "craft"}

    def test_composed_tree_without_marker_is_ignored(self, tmp_path):
        env = _hermetic_env(tmp_path)
        _make_composed_tool(tmp_path, "lore", registered=True)
        _make_composed_tool(tmp_path, "stale", registered=False)

        code, out, err, calls = _run_uninstall(env=env, assume_yes=True)
        assert calls == ["lore"]

    def test_nothing_to_uninstall(self, tmp_path):
        env = _hermetic_env(tmp_path)
        code, out, err, calls = _run_uninstall(env=env, assume_yes=True)
        assert code == 0
        assert calls == []
        assert "nothing" in out.lower()


# ---------------------------------------------------------------------------
# Teardown effects
# ---------------------------------------------------------------------------


class TestTeardown:
    def test_composed_trees_removed(self, tmp_path):
        env = _hermetic_env(tmp_path)
        mkt = _make_composed_tool(tmp_path, "lore")
        assert mkt.exists()

        _run_uninstall(env=env, assume_yes=True)
        assert not mkt.exists()

    def test_config_deleted(self, tmp_path):
        from trailhead.config import TrailheadConfig, save_config

        env = _hermetic_env(tmp_path)
        save_config(TrailheadConfig(capabilities={"lore": ["capture"]}), env=env)
        _make_composed_tool(tmp_path, "lore")
        config_file = Path(env["TRAILHEAD_CONFIG_DIR"]) / "config.toml"
        assert config_file.exists()

        _run_uninstall(env=env, assume_yes=True)
        assert not config_file.exists()

    def test_shim_dir_removed(self, tmp_path):
        env = _hermetic_env(tmp_path)
        _make_composed_tool(tmp_path, "lore")
        shim_dir = tmp_path / "state" / "bin"
        shim_dir.mkdir(parents=True)
        (shim_dir / "camp").write_text("#!/bin/bash\n")

        _run_uninstall(env=env, assume_yes=True)
        assert not shim_dir.exists()

    def test_remove_path_integration_called(self, tmp_path):
        env = _hermetic_env(tmp_path)
        _make_composed_tool(tmp_path, "lore")

        from trailhead import uninstall as uninstall_mod
        with patch("trailhead.uninstall.unregister"), \
             patch("trailhead.uninstall.remove_path_integration") as mock_rpi, \
             patch("trailhead.uninstall._is_tty", return_value=False):
            uninstall_mod.run_uninstall(env=env, assume_yes=True)
            mock_rpi.assert_called_once()

    def test_update_state_removed(self, tmp_path):
        env = _hermetic_env(tmp_path)
        _make_composed_tool(tmp_path, "lore")
        state_file = tmp_path / "state" / "update_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("{}")

        _run_uninstall(env=env, assume_yes=True)
        assert not state_file.exists()


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


class TestConfirmation:
    def test_tty_decline_aborts(self, tmp_path):
        env = _hermetic_env(tmp_path)
        mkt = _make_composed_tool(tmp_path, "lore")

        code, out, err, calls = _run_uninstall(
            env=env, is_tty=True, assume_yes=False, stdin_text="n\n"
        )
        assert code == 0
        assert calls == []
        assert mkt.exists(), "decline must not remove anything"
        assert "abort" in out.lower()

    def test_tty_accept_proceeds(self, tmp_path):
        env = _hermetic_env(tmp_path)
        _make_composed_tool(tmp_path, "lore")

        code, out, err, calls = _run_uninstall(
            env=env, is_tty=True, assume_yes=False, stdin_text="y\n"
        )
        assert calls == ["lore"]

    def test_bare_enter_aborts(self, tmp_path):
        """Default (bare enter) is No — destructive op requires explicit yes."""
        env = _hermetic_env(tmp_path)
        mkt = _make_composed_tool(tmp_path, "lore")

        code, out, err, calls = _run_uninstall(
            env=env, is_tty=True, assume_yes=False, stdin_text="\n"
        )
        assert calls == []
        assert mkt.exists()

    def test_assume_yes_skips_prompt(self, tmp_path):
        env = _hermetic_env(tmp_path)
        _make_composed_tool(tmp_path, "lore")

        code, out, err, calls = _run_uninstall(
            env=env, is_tty=True, assume_yes=True, stdin_text=""
        )
        assert calls == ["lore"]

    def test_non_tty_without_yes_refuses(self, tmp_path):
        """Piped/non-interactive without --yes must refuse, not tear down silently."""
        env = _hermetic_env(tmp_path)
        mkt = _make_composed_tool(tmp_path, "lore")

        code, out, err, calls = _run_uninstall(
            env=env, is_tty=False, assume_yes=False,
        )
        assert code == 1
        assert calls == []
        assert mkt.exists()
        assert "--yes" in err

    def test_json_without_yes_refuses(self, tmp_path):
        """--json can't prompt → refuse without --yes even on a TTY."""
        env = _hermetic_env(tmp_path)
        mkt = _make_composed_tool(tmp_path, "lore")

        code, out, err, calls = _run_uninstall(
            env=env, is_tty=True, assume_yes=False, as_json=True,
        )
        assert code == 1
        assert calls == []
        assert mkt.exists()


class TestConcurrencyLock:
    def test_held_lock_aborts_without_teardown(self, tmp_path):
        """A concurrent operation holding wire_lock → uninstall refuses cleanly."""
        env = _hermetic_env(tmp_path)
        mkt = _make_composed_tool(tmp_path, "lore")
        # Simulate the lock being held by another trailhead operation.
        lock = tmp_path / "state" / "trailhead.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("locked by pid 99999\n")

        code, out, err, calls = _run_uninstall(env=env, assume_yes=True)
        assert code == 1
        assert calls == []
        assert mkt.exists(), "must not tear down while another op holds the lock"


# ---------------------------------------------------------------------------
# Best-effort de-registration
# ---------------------------------------------------------------------------


class TestBestEffort:
    def test_harness_failure_warns_but_continues(self, tmp_path):
        env = _hermetic_env(tmp_path)
        mkt = _make_composed_tool(tmp_path, "lore")

        def boom(tool):
            raise RuntimeError("plugin not found")

        code, out, err, calls = _run_uninstall(
            env=env, assume_yes=True, unregister_side_effect=boom,
        )
        # Still exits 0, still removes local state, surfaces a warning.
        assert code == 0
        assert not mkt.exists()
        assert "warning" in err.lower() or "lore" in err.lower()


# ---------------------------------------------------------------------------
# Output modes
# ---------------------------------------------------------------------------


class TestOutputModes:
    def test_json_parseable(self, tmp_path):
        env = _hermetic_env(tmp_path)
        _make_composed_tool(tmp_path, "lore")

        code, out, err, calls = _run_uninstall(env=env, assume_yes=True, as_json=True)
        data = json.loads(out)
        assert "removed" in data
        assert "lore" in data["removed"]

    def test_quiet_no_progress_lines(self, tmp_path):
        env = _hermetic_env(tmp_path)
        _make_composed_tool(tmp_path, "lore")

        code, out, err, calls = _run_uninstall(env=env, assume_yes=True, quiet=True)
        assert "removing" not in out.lower()
        # summary still present
        assert "uninstalled" in out.lower() or "kept" in out.lower()

    def test_summary_says_data_kept(self, tmp_path):
        env = _hermetic_env(tmp_path)
        _make_composed_tool(tmp_path, "lore")

        code, out, err, calls = _run_uninstall(env=env, assume_yes=True)
        assert "kept" in out.lower() or "data" in out.lower()
