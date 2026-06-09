"""Slice 3 tests: install-state-gated clickable links in the lore banner.

Covers (TDD — written before the implementation):
- note_link: active (state present + LINK_SERVER_ENABLED True) → HTTP URL
- note_link: state absent → plain path unchanged
- note_link: kill-switch False → plain path unchanged
- note_link: custom port honored
- Banner integration: session-context hook emits linked form when active,
  plain form when not
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
HOOKS_DIR = PLUGIN_ROOT / "hooks"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


def load_script(name: str):
    """Load a module from plugins/lore/scripts/ by stem, freshly each call."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    for cached in [name, "config", "link_server"]:
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_hook(name: str):
    """Load a hook module from hooks/ by stem, freshly each call."""
    for d in (str(HOOKS_DIR), str(SCRIPTS_DIR)):
        if d not in sys.path:
            sys.path.insert(0, d)
    for cached in (name, "sessions", "vault", "frontmatter", "status_validator",
                   "config", "link_server", "recall"):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location(name, HOOKS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "sessions").mkdir(parents=True)
    return vault


def _write_state(state_dir: Path, port: int = 7777) -> None:
    """Write a minimal valid install-state file into state_dir."""
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "link-server.json"
    state_file.write_text(json.dumps({
        "schema": 1,
        "port": port,
        "plist_path": "/Library/LaunchAgents/com.lore.link-server.plist",
        "vault": "/tmp/v",
        "plugin_root": "/p",
    }) + "\n")


# ---------------------------------------------------------------------------
# note_link — unit tests
# ---------------------------------------------------------------------------

class TestNoteLink:
    def _load_ls(self, enabled: bool):
        """Load link_server with LINK_SERVER_ENABLED set to the given value."""
        ls = load_script("link_server")
        ls.config.LINK_SERVER_ENABLED = enabled
        return ls

    def test_active_returns_http_url(self, tmp_path):
        """State present + LINK_SERVER_ENABLED True → http://localhost:<port>/path (no .md)."""
        state_dir = tmp_path / "config"
        _write_state(state_dir, port=7777)

        ls = self._load_ls(enabled=True)
        result = ls.note_link("sessions/2026-06/x.md", state_dir=state_dir)

        assert result == "http://localhost:7777/sessions/2026-06/x"

    def test_state_absent_returns_plain_path(self, tmp_path):
        """No state file → plain vault-relative path unchanged."""
        state_dir = tmp_path / "empty_config"
        # Do not create state_dir or state file

        ls = self._load_ls(enabled=True)
        result = ls.note_link("sessions/2026-06/x.md", state_dir=state_dir)

        assert result == "sessions/2026-06/x.md"

    def test_kill_switch_off_returns_plain_path(self, tmp_path):
        """State present but LINK_SERVER_ENABLED False → plain path unchanged."""
        state_dir = tmp_path / "config"
        _write_state(state_dir, port=7777)

        ls = self._load_ls(enabled=False)
        result = ls.note_link("sessions/2026-06/x.md", state_dir=state_dir)

        assert result == "sessions/2026-06/x.md"

    def test_port_honored(self, tmp_path):
        """State file with custom port → URL uses that port."""
        state_dir = tmp_path / "config"
        _write_state(state_dir, port=8123)

        ls = self._load_ls(enabled=True)
        result = ls.note_link("sessions/2026-06/x.md", state_dir=state_dir)

        assert result == "http://localhost:8123/sessions/2026-06/x"

    def test_subfolders_preserved(self, tmp_path):
        """Subfolders in the vault-relative path are preserved in the URL."""
        state_dir = tmp_path / "config"
        _write_state(state_dir, port=7777)

        ls = self._load_ls(enabled=True)
        result = ls.note_link("subsystems/workflow-brain-context.md", state_dir=state_dir)

        assert result == "http://localhost:7777/subsystems/workflow-brain-context"

    def test_path_without_md_suffix_becomes_url(self, tmp_path):
        """A path without .md is passed through to the URL as-is."""
        state_dir = tmp_path / "config"
        _write_state(state_dir, port=7777)

        ls = self._load_ls(enabled=True)
        result = ls.note_link("sessions/2026-06/x", state_dir=state_dir)

        assert result == "http://localhost:7777/sessions/2026-06/x"

    def test_fallback_port_7777_when_key_missing(self, tmp_path):
        """State file missing 'port' key → fall back to 7777."""
        state_dir = tmp_path / "config"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "link-server.json"
        # Write a state without 'port'
        state_file.write_text(json.dumps({
            "schema": 1,
            "plist_path": "/Library/LaunchAgents/com.lore.link-server.plist",
            "vault": "/tmp/v",
            "plugin_root": "/p",
        }) + "\n")

        ls = self._load_ls(enabled=True)
        result = ls.note_link("sessions/2026-06/x.md", state_dir=state_dir)

        assert result == "http://localhost:7777/sessions/2026-06/x"

    def test_non_int_port_falls_back_to_7777(self, tmp_path):
        """A hand-corrupted non-int 'port' degrades to 7777, not a broken host:port."""
        state_dir = tmp_path / "config"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "link-server.json").write_text(json.dumps({
            "schema": 1,
            "port": "abc",
            "plist_path": "/Library/LaunchAgents/com.lore.link-server.plist",
            "vault": "/tmp/v",
            "plugin_root": "/p",
        }) + "\n")

        ls = self._load_ls(enabled=True)
        result = ls.note_link("sessions/2026-06/x.md", state_dir=state_dir)

        assert result == "http://localhost:7777/sessions/2026-06/x"


# ---------------------------------------------------------------------------
# Banner integration: session-context.py emits linked / plain form
# ---------------------------------------------------------------------------

def _run_session_context(stdin_payload: dict, env: dict, cwd: Path):
    """Run session-context.py main() in-process with patched stdin/stdout/env."""
    mod = load_hook("session-context")
    out = io.StringIO()
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))):
            with mock.patch("sys.stdout", out):
                with mock.patch.object(os, "getcwd", return_value=str(cwd)):
                    mod.main()
    return out.getvalue()


class TestBannerIntegration:
    def test_banner_emits_http_link_when_active(self, tmp_path):
        """When state is present + LINK_SERVER_ENABLED True, session note pointer
        is an http://localhost:<port>/... URL."""
        vault = _make_vault(tmp_path)
        cwd = tmp_path / "my-worktree"
        cwd.mkdir()
        state_dir = tmp_path / "lore-config"
        _write_state(state_dir, port=7777)

        env = {
            "LORE_VAULT": str(vault),
            "LORE_LINK_STATE_DIR": str(state_dir),
            "PATH": os.environ.get("PATH", ""),
        }
        out = _run_session_context({"session_id": "abc"}, env, cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]

        assert "http://localhost:7777/" in ctx
        assert "my-worktree" in ctx

    def test_banner_emits_plain_path_when_state_absent(self, tmp_path):
        """When no state file exists, session note pointer is the plain path."""
        vault = _make_vault(tmp_path)
        cwd = tmp_path / "my-worktree"
        cwd.mkdir()
        state_dir = tmp_path / "empty-config"
        # Do not create state_dir

        env = {
            "LORE_VAULT": str(vault),
            "LORE_LINK_STATE_DIR": str(state_dir),
            "PATH": os.environ.get("PATH", ""),
        }
        out = _run_session_context({"session_id": "abc"}, env, cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]

        # Plain filename present, no http link
        assert "http://localhost" not in ctx
        notes = list((vault / "sessions").glob("*/*.md"))
        assert len(notes) == 1
        assert notes[0].name in ctx
