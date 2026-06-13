"""Tests for trailhead/uninstall.py — nuke-everything teardown.

Discovery is on-disk (per-harness composed markers). The harness-CLI runner is
a recording stub. trailhead no longer edits the shell rc, so teardown only
removes the shim dir (under the tmp state dir) — nothing to patch there.
"""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from trailhead.uninstall import run_uninstall


def _env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {**os.environ, "TRAILHEAD_STATE_DIR": str(tmp_path), "HOME": str(home)}


def _make_harness_tree(tmp_path: Path, hname: str, tools: list[str], *, registered=True):
    root = tmp_path / "composed" / hname
    (root / "plugins").mkdir(parents=True)
    if registered:
        (root / ".trailhead-registered").write_text("{}")
    for t in tools:
        (root / "plugins" / t / ".claude-plugin").mkdir(parents=True)
        (root / "plugins" / t / ".claude-plugin" / "plugin.json").write_text("{}")
        (root / f".trailhead-installed-{t}").write_text("{}")
    return root


@contextmanager
def _recording():
    calls: list[list[str]] = []

    def runner(args, **kw):
        calls.append(list(args))

    # trailhead no longer edits the shell rc, so there's nothing to patch there.
    yield calls, runner, None


# ---------------------------------------------------------------------------
# Nothing to uninstall
# ---------------------------------------------------------------------------


class TestNothing:
    def test_nothing_to_uninstall(self, tmp_path, capsys):
        with _recording() as (calls, runner, _):
            rc = run_uninstall(env=_env(tmp_path), assume_yes=True, runner=runner)
        assert rc == 0
        assert "nothing to uninstall" in capsys.readouterr().out
        assert calls == []


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


class TestConfirmation:
    def test_refuses_without_yes_noninteractive(self, tmp_path, capsys):
        _make_harness_tree(tmp_path, "claude_code", ["lore"])
        with _recording() as (calls, runner, _):
            with patch("trailhead.uninstall._is_tty", return_value=False):
                rc = run_uninstall(env=_env(tmp_path), runner=runner)
        assert rc == 1
        assert "refusing to uninstall" in capsys.readouterr().err
        # Nothing torn down.
        assert (tmp_path / "composed" / "claude_code").exists()


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


class TestTeardown:
    def test_unregisters_each_installed_tool(self, tmp_path):
        _make_harness_tree(tmp_path, "claude_code", ["lore", "camp"])
        with _recording() as (calls, runner, _):
            run_uninstall(env=_env(tmp_path), assume_yes=True, runner=runner)
        uninstalls = [c for c in calls if "uninstall" in c]
        assert any("lore@trailhead" in c for c in uninstalls)
        assert any("camp@trailhead" in c for c in uninstalls)

    def test_keep_data_flag_present(self, tmp_path):
        _make_harness_tree(tmp_path, "claude_code", ["lore"])
        with _recording() as (calls, runner, _):
            run_uninstall(env=_env(tmp_path), assume_yes=True, runner=runner)
        uninstalls = [c for c in calls if "uninstall" in c]
        assert all("--keep-data" in c for c in uninstalls)

    def test_marketplace_removed_once_per_harness(self, tmp_path):
        _make_harness_tree(tmp_path, "claude_code", ["lore", "camp"])
        with _recording() as (calls, runner, _):
            run_uninstall(env=_env(tmp_path), assume_yes=True, runner=runner)
        removes = [c for c in calls if "marketplace" in c and "remove" in c]
        assert len(removes) == 1

    def test_composed_tree_removed(self, tmp_path):
        _make_harness_tree(tmp_path, "claude_code", ["lore"])
        with _recording() as (calls, runner, _):
            run_uninstall(env=_env(tmp_path), assume_yes=True, runner=runner)
        assert not (tmp_path / "composed" / "claude_code").exists()
        # composed base removed when empty
        assert not (tmp_path / "composed").exists()

    def test_shim_dir_removed(self, tmp_path):
        _make_harness_tree(tmp_path, "claude_code", ["lore"])
        shim = tmp_path / "bin"
        shim.mkdir()
        (shim / "camp").write_text("#!/bin/sh\n")
        with _recording() as (calls, runner, _):
            run_uninstall(env=_env(tmp_path), assume_yes=True, runner=runner)
        assert not shim.exists()

    def test_multiple_harnesses_torn_down(self, tmp_path):
        _make_harness_tree(tmp_path, "claude_code", ["lore"])
        # A second (unknown) harness dir — torn down without CLI calls.
        _make_harness_tree(tmp_path, "codex", ["craft"], registered=False)
        with _recording() as (calls, runner, _):
            rc = run_uninstall(env=_env(tmp_path), assume_yes=True, runner=runner)
        assert rc == 0
        assert not (tmp_path / "composed" / "claude_code").exists()
        assert not (tmp_path / "composed" / "codex").exists()


# ---------------------------------------------------------------------------
# Best-effort / unknown harness
# ---------------------------------------------------------------------------


class TestBestEffort:
    def test_unknown_harness_warns_but_removes_tree(self, tmp_path, capsys):
        _make_harness_tree(tmp_path, "codex", ["craft"])
        with _recording() as (calls, runner, _):
            rc = run_uninstall(env=_env(tmp_path), assume_yes=True, runner=runner)
        assert rc == 0
        assert "unknown harness" in capsys.readouterr().err
        assert not (tmp_path / "composed" / "codex").exists()
        # No CLI calls for an unknown harness.
        assert calls == []

    def test_runner_error_is_warning_not_failure(self, tmp_path, capsys):
        _make_harness_tree(tmp_path, "claude_code", ["lore"])

        def boom(args, **kw):
            raise RuntimeError("cli unavailable")

        rc = run_uninstall(env=_env(tmp_path), assume_yes=True, runner=boom)
        assert rc == 0
        assert "warning" in capsys.readouterr().err
        # Tree still removed despite CLI failure.
        assert not (tmp_path / "composed" / "claude_code").exists()


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


class TestJson:
    def test_json_shape(self, tmp_path, capsys):
        _make_harness_tree(tmp_path, "claude_code", ["lore", "camp"])
        with _recording() as (calls, runner, _):
            run_uninstall(env=_env(tmp_path), assume_yes=True, as_json=True, runner=runner)
        data = json.loads(capsys.readouterr().out)
        assert set(data["removed"]) == {"claude_code"}
        assert set(data["removed"]["claude_code"]) == {"lore", "camp"}
