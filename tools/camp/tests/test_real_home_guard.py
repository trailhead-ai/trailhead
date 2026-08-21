"""The suite-wide guard rail against a test reaching the developer's real home.

camp's launch-time trust pre-seed writes Claude Code's global config file, which
carries OAuth secrets. Its resolver falls back to `Path.home()` when the caller
injects no `HOME`, so a future test that forgets to sandbox one would merge a
trust key into the operator's real `~/.claude.json`. `_forbid_real_home` makes
that fail loudly instead (Axiom 6).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from . import conftest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.launch.claude_trust import pretrust_workspace  # noqa: E402


class TestTheGuardFires:
    def test_pretrust_without_an_injected_home_fails_loudly(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        with pytest.raises(AssertionError) as exc_info:
            pretrust_workspace(workspace, workspace_root=workspace, env={})
        assert "HOME" in str(exc_info.value)

    def test_env_none_fails_loudly_too(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        with pytest.raises(AssertionError):
            pretrust_workspace(workspace, workspace_root=workspace)


class TestTheGuardDoesNotGetInTheWay:
    def test_an_injected_home_writes_into_the_sandbox(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        home = tmp_path / "home"
        home.mkdir()
        assert pretrust_workspace(
            workspace, workspace_root=workspace, env={"HOME": str(home)}
        ) is True
        assert (home / ".claude.json").exists()

    @pytest.mark.real_home
    def test_the_marker_opts_a_test_back_in(self):
        assert Path.home().is_absolute()


class TestTheLiveTrustScan:
    """The watchdog's detector — the part that catches a *subprocess* write."""

    def _point_at(self, monkeypatch, path):
        monkeypatch.setattr(conftest, "_LIVE_CLAUDE_JSON", path)

    def test_the_project_keys_of_the_live_file_are_read(self, tmp_path, monkeypatch):
        live = tmp_path / "claude.json"
        live.write_text(json.dumps({"projects": {"/a": {}, "/b": {}}, "other": 1}))
        self._point_at(monkeypatch, live)
        assert conftest._live_project_keys() == frozenset({"/a", "/b"})

    @pytest.mark.parametrize("body", ["not json", '{"projects": []}', "{}", "[]"])
    def test_an_unreadable_or_odd_file_reports_nothing_rather_than_erroring(
        self, tmp_path, monkeypatch, body
    ):
        live = tmp_path / "claude.json"
        live.write_text(body)
        self._point_at(monkeypatch, live)
        assert conftest._live_project_keys() == frozenset()

    def test_a_missing_file_reports_nothing(self, tmp_path, monkeypatch):
        self._point_at(monkeypatch, tmp_path / "absent.json")
        assert conftest._live_project_keys() == frozenset()

    def test_the_fingerprint_moves_when_the_file_changes(self, tmp_path):
        live = tmp_path / "claude.json"
        live.write_text("{}")
        before = conftest._fingerprint(live)
        live.write_text('{"projects": {}}')
        assert conftest._fingerprint(live) != before

    def test_the_fingerprint_of_a_missing_file_is_none(self, tmp_path):
        assert conftest._fingerprint(tmp_path / "absent.json") is None
