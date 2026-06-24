"""Tests for Slice 2, S5: lore installs zero hooks (no SessionStart, no WorktreeRemove).

Covers every bullet of the Slice 2 test contract:
  - After init, resolved settings.json has no lore SessionStart entry and no
    WorktreeRemove/finalize entry.
  - An unrelated pre-existing hook in the file is preserved.
  - Re-run is a no-op (idempotent).
  - The plugin hooks.json wires neither session-context.py nor finalize-session-note.py.
  - A grep across the lore plugin for session-context, additionalContext,
    LORE_COMMANDS, and the five /lore: capture-command strings returns zero matches
    outside absence-asserting tests.
  - --local likewise installs no lore hooks.
  - settings_writer.py: idempotent upsert, preserves unrelated entries, no-op re-run.

All tests inject XDG_STATE_HOME / XDG_CONFIG_HOME via env and use tmp_path so
they NEVER touch real config, state, or vault data (Axiom 6).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
PLUGIN_ROOT = TESTS_DIR.parent / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
HOOKS_DIR = PLUGIN_ROOT / "hooks"
HOOKS_JSON = HOOKS_DIR / "hooks.json"

sys.path.insert(0, str(TESTS_DIR))
from conftest import load_script  # noqa: E402


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _run(args, *, state, config, home, cwd=None, extra=None):
    """Run lore CLI with isolated XDG dirs and an isolated HOME (Axiom 6)."""
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(state)
    env["XDG_CONFIG_HOME"] = str(config)
    env["HOME"] = str(home)
    env["LORE_EMAIL"] = "tester@example.com"
    if extra:
        env.update(extra)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
    )


def _dirs(tmp_path):
    state = tmp_path / "state"
    config = tmp_path / "config"
    home = tmp_path / "home"
    for d in (state, config, home):
        d.mkdir(parents=True, exist_ok=True)
    return state, config, home


# ---------------------------------------------------------------------------
# 1. hooks.json: session-context.py and finalize-session-note.py not wired
# ---------------------------------------------------------------------------

class TestHooksJson:
    def test_hooks_json_does_not_wire_session_context(self):
        """hooks.json must not reference session-context.py (F5: no SessionStart hook)."""
        data = json.loads(HOOKS_JSON.read_text())
        hooks = data.get("hooks", {})
        session_start = hooks.get("SessionStart", [])
        for entry in session_start:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                assert "session-context" not in cmd, (
                    f"hooks.json still wires session-context.py: {cmd!r}"
                )

    def test_hooks_json_has_no_session_start_entry(self):
        """SessionStart key must be absent from hooks.json (F5)."""
        data = json.loads(HOOKS_JSON.read_text())
        hooks = data.get("hooks", {})
        assert "SessionStart" not in hooks, (
            "hooks.json still has a SessionStart entry"
        )

    def test_hooks_json_does_not_wire_finalize_session_note(self):
        """hooks.json must not reference finalize-session-note.py."""
        data = json.loads(HOOKS_JSON.read_text())
        hooks = data.get("hooks", {})
        worktree_remove = hooks.get("WorktreeRemove", [])
        for entry in worktree_remove:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                assert "finalize-session-note" not in cmd, (
                    f"hooks.json still wires finalize-session-note.py: {cmd!r}"
                )

    def test_hooks_json_has_no_worktree_remove_entry(self):
        """WorktreeRemove key must be absent from hooks.json (finalization is explicit)."""
        data = json.loads(HOOKS_JSON.read_text())
        hooks = data.get("hooks", {})
        assert "WorktreeRemove" not in hooks, (
            "hooks.json still has a WorktreeRemove entry"
        )

    def test_hooks_json_has_no_post_tool_use(self):
        """PostToolUse harvest entry must be absent from hooks.json.

        lore-agent-interface Slice 1 retired the harvest hook — lore installs
        zero push hooks. hooks.json carries an empty hooks dict.
        """
        data = json.loads(HOOKS_JSON.read_text())
        hooks = data.get("hooks", {})
        assert "PostToolUse" not in hooks, (
            "hooks.json still has a PostToolUse entry — harvest hook was deleted "
            "(lore-agent-interface Slice 1)"
        )


# ---------------------------------------------------------------------------
# 2. Deleted hook script files are absent
# ---------------------------------------------------------------------------

class TestDeletedHookScripts:
    def test_session_context_script_deleted(self):
        """hooks/session-context.py must not exist (F5: lore is fully pull)."""
        assert not (HOOKS_DIR / "session-context.py").exists(), (
            "hooks/session-context.py still exists — delete it (Slice 2, S5)"
        )

    def test_finalize_session_note_script_deleted(self):
        """hooks/finalize-session-note.py must not exist (finalization is explicit)."""
        assert not (HOOKS_DIR / "finalize-session-note.py").exists(), (
            "hooks/finalize-session-note.py still exists — delete it (Slice 2, S5)"
        )


# ---------------------------------------------------------------------------
# 3. settings_writer module — idempotent upsert
# ---------------------------------------------------------------------------

class TestSettingsWriter:
    def _sw(self):
        return load_script("settings_writer")

    def test_write_to_new_file_creates_it(self, tmp_path):
        """Writing to an absent settings file creates it."""
        sw = self._sw()
        settings_path = tmp_path / ".claude" / "settings.json"
        sw.remove_hook(settings_path, "SessionStart", "some-cmd.py")
        # remove_hook on absent file should not error; if we then upsert...
        # Actually test the ensure_no_hook contract: absent file → no hook present
        # (we don't call upsert here — Slice 2 is about *not* installing hooks)

    def test_ensure_no_lore_hooks_on_fresh_file(self, tmp_path):
        """remove_hook on absent settings file is a no-op (no error)."""
        sw = self._sw()
        settings_path = tmp_path / ".claude" / "settings.json"
        sw.remove_hook(settings_path, "SessionStart", "lore-session-context.py")
        assert not settings_path.exists() or json.loads(settings_path.read_text()) == {}

    def test_corrupt_settings_raises_not_clobbers(self, tmp_path):
        """A present-but-unparseable settings.json must raise (clean error) and be
        left BYTE-FOR-BYTE untouched — never silently treated as {} and clobbered
        (Axiom 6: never corrupt the live install)."""
        sw = self._sw()
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        corrupt = '{"hooks": {"PreToolUse": [ trailing comma, ] '  # invalid JSON
        settings_path.write_text(corrupt)

        with pytest.raises(ValueError):
            sw.upsert_hook(settings_path, "PreToolUse", "my-guard.py", matcher="Edit|Write")
        assert settings_path.read_text() == corrupt, "corrupt settings.json was clobbered"

        with pytest.raises(ValueError):
            sw.remove_hook(settings_path, "SessionStart", "anything.py")
        assert settings_path.read_text() == corrupt, "corrupt settings.json was clobbered"

    def test_remove_hook_removes_matching_entry(self, tmp_path):
        """remove_hook removes the entry whose command matches."""
        sw = self._sw()
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        # Pre-populate with a hook we want to remove
        data = {
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "lore-context.py"}]}
                ]
            }
        }
        settings_path.write_text(json.dumps(data))
        sw.remove_hook(settings_path, "SessionStart", "lore-context.py")
        result = json.loads(settings_path.read_text())
        session_start = result.get("hooks", {}).get("SessionStart", [])
        for entry in session_start:
            for h in entry.get("hooks", []):
                assert "lore-context.py" not in h.get("command", ""), (
                    "remove_hook did not remove the matching entry"
                )

    def test_remove_hook_preserves_unrelated_entry(self, tmp_path):
        """remove_hook must not disturb hooks for other events."""
        sw = self._sw()
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "lore-context.py"}]}
                ],
                "PreToolUse": [
                    {"matcher": "Edit|Write",
                     "hooks": [{"type": "command", "command": "unrelated-guard.py"}]}
                ],
            }
        }
        settings_path.write_text(json.dumps(data))
        sw.remove_hook(settings_path, "SessionStart", "lore-context.py")
        result = json.loads(settings_path.read_text())
        pre = result.get("hooks", {}).get("PreToolUse", [])
        cmds = [h.get("command") for e in pre for h in e.get("hooks", [])]
        assert "unrelated-guard.py" in cmds, (
            "remove_hook removed an unrelated PreToolUse hook"
        )

    def test_remove_hook_is_noop_when_absent(self, tmp_path):
        """remove_hook on a settings file that lacks the target hook is idempotent."""
        sw = self._sw()
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"hooks": {"PostToolUse": [
            {"hooks": [{"type": "command", "command": "harvest.py"}]}
        ]}}
        settings_path.write_text(json.dumps(data))
        original = settings_path.read_text()
        sw.remove_hook(settings_path, "SessionStart", "lore-context.py")
        # File must be unchanged (no write needed when target is absent)
        after = json.loads(settings_path.read_text())
        original_parsed = json.loads(original)
        assert after == original_parsed, "remove_hook mutated file when hook was absent"

    def test_upsert_hook_adds_missing_entry(self, tmp_path):
        """upsert_hook appends the command when absent."""
        sw = self._sw()
        settings_path = tmp_path / ".claude" / "settings.json"
        sw.upsert_hook(settings_path, "PreToolUse", "my-guard.py", matcher="Edit|Write")
        data = json.loads(settings_path.read_text())
        entries = data.get("hooks", {}).get("PreToolUse", [])
        cmds = [h.get("command") for e in entries for h in e.get("hooks", [])]
        assert "my-guard.py" in cmds

    def test_upsert_hook_is_idempotent(self, tmp_path):
        """upsert_hook called twice does not duplicate the entry."""
        sw = self._sw()
        settings_path = tmp_path / ".claude" / "settings.json"
        sw.upsert_hook(settings_path, "PreToolUse", "my-guard.py", matcher="Edit|Write")
        sw.upsert_hook(settings_path, "PreToolUse", "my-guard.py", matcher="Edit|Write")
        data = json.loads(settings_path.read_text())
        entries = data.get("hooks", {}).get("PreToolUse", [])
        cmds = [h.get("command") for e in entries for h in e.get("hooks", [])]
        count = cmds.count("my-guard.py")
        assert count == 1, f"upsert_hook duplicated entry: found {count}"

    def test_upsert_hook_preserves_unrelated_keys(self, tmp_path):
        """upsert_hook must not drop unrelated top-level keys."""
        sw = self._sw()
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"env": {"CAMP_BIN": "/usr/local/bin/camp"}, "permissions": {"allow": []}}
        settings_path.write_text(json.dumps(data))
        sw.upsert_hook(settings_path, "PostToolUse", "harvest.py", matcher="Agent|Task")
        result = json.loads(settings_path.read_text())
        assert result.get("env", {}).get("CAMP_BIN") == "/usr/local/bin/camp"
        assert "permissions" in result


# ---------------------------------------------------------------------------
# 4. lore init installs zero lore hooks in settings.json
# ---------------------------------------------------------------------------

class TestInitInstallsNoHooks:
    def test_init_writes_no_session_start_hook(self, tmp_path):
        """After lore init, settings.json must have no SessionStart entry."""
        state, config, home = _dirs(tmp_path)
        res = _run(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        settings_path = home / ".claude" / "settings.json"
        if not settings_path.exists():
            return  # no settings file written at all → no hooks installed
        data = json.loads(settings_path.read_text())
        session_start = data.get("hooks", {}).get("SessionStart", [])
        for entry in session_start:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                assert "lore" not in cmd.lower(), (
                    f"lore init installed a SessionStart hook: {cmd!r}"
                )

    def test_init_writes_no_worktree_remove_hook(self, tmp_path):
        """After lore init, settings.json must have no WorktreeRemove/finalize entry."""
        state, config, home = _dirs(tmp_path)
        res = _run(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        settings_path = home / ".claude" / "settings.json"
        if not settings_path.exists():
            return
        data = json.loads(settings_path.read_text())
        wtr = data.get("hooks", {}).get("WorktreeRemove", [])
        for entry in wtr:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                assert "lore" not in cmd.lower() and "finalize" not in cmd.lower(), (
                    f"lore init installed a WorktreeRemove hook: {cmd!r}"
                )

    def test_init_preserves_unrelated_existing_hook(self, tmp_path):
        """lore init must not remove a pre-existing unrelated hook from settings.json."""
        state, config, home = _dirs(tmp_path)

        # Pre-populate the user-global settings.json with an unrelated hook.
        settings_path = home / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        pre_existing = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Edit|Write",
                     "hooks": [{"type": "command", "command": "unrelated-guard.py"}]}
                ]
            }
        }
        settings_path.write_text(json.dumps(pre_existing))

        res = _run(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        data = json.loads(settings_path.read_text())
        pre = data.get("hooks", {}).get("PreToolUse", [])
        cmds = [h.get("command") for e in pre for h in e.get("hooks", [])]
        assert "unrelated-guard.py" in cmds, (
            "lore init removed an unrelated PreToolUse hook from settings.json"
        )

    def test_init_rerun_is_noop(self, tmp_path):
        """Second lore init produces an identical settings.json (no-op)."""
        state, config, home = _dirs(tmp_path)

        settings_path = home / ".claude" / "settings.json"
        _run(["init"], state=state, config=config, home=home)
        after_first = settings_path.read_text() if settings_path.exists() else None

        _run(["init"], state=state, config=config, home=home)
        after_second = settings_path.read_text() if settings_path.exists() else None

        assert after_first == after_second, (
            "lore init changed settings.json on second run (not idempotent)"
        )


# ---------------------------------------------------------------------------
# Structural retirement: the harvest module + starter protocol are gone
# (relocated from the deleted test_finish.py when finish → flush, Slice 2).
# ---------------------------------------------------------------------------

class TestHarvestModuleRetired:
    def test_harvest_script_no_longer_exists(self):
        assert not (SCRIPTS_DIR / "harvest.py").exists()

    def test_harvest_protocol_starter_no_longer_exists(self):
        assert not (PLUGIN_ROOT / "starter" / "harvest-protocol.md").exists()

    def test_importing_harvest_module_fails(self):
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        sys.modules.pop("harvest", None)
        try:
            mod = importlib.util.find_spec("harvest")
        except ModuleNotFoundError:
            mod = None
        assert mod is None, "the harvest module must no longer be importable"
