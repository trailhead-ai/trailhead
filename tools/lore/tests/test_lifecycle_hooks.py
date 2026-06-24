"""Slice 4 tests: session lifecycle hooks.

Covers (TDD — written before the hooks):
- sessions.ensure_session_note: creates a note with valid `session` frontmatter
  and the five required body headings; resumes a note modified inside the window.
- permission-log.py: appends an entry.

Slice 2, S5 (F5): the SessionStart hook and WorktreeRemove hook were deleted;
their test coverage was removed here accordingly (lore is fully pull — orientation
lives in agent-rules).

Slice 1 (lore-agent-interface): the PostToolUse harvest-candidates hook was
deleted; lore installs zero push hooks. harvest-candidates.py is gone and
hooks.json carries no PostToolUse entry.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
HOOKS_DIR = PLUGIN_ROOT / "hooks"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


def load_hook(name: str):
    """Load a hook module from hooks/ by stem, freshly each call."""
    for d in (str(HOOKS_DIR), str(SCRIPTS_DIR)):
        if d not in sys.path:
            sys.path.insert(0, d)
    for cached in (name, "sessions", "vault", "frontmatter", "status_validator"):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location(name, HOOKS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_sessions():
    for d in (str(SCRIPTS_DIR),):
        if d not in sys.path:
            sys.path.insert(0, d)
    for cached in ("sessions", "vault", "frontmatter", "status_validator"):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location("sessions", SCRIPTS_DIR / "sessions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_script(name: str):
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "sessions").mkdir(parents=True)
    return vault


def _git_vault(tmp_path: Path) -> Path:
    """A vault that is its own git repo (toplevel == vault)."""
    vault = _make_vault(tmp_path)
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", "t@e.st"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", "Tester"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "commit.gpgsign", "false"],
                   check=True, capture_output=True)
    return vault



# ---------------------------------------------------------------------------
# sessions.ensure_session_note + finalize_note were retired in Slice 2 (the
# frontmatter-note CREATE/finalize lifecycle): capture moved to singular indexed
# ``session/`` records (Slice 1) and ``lore flush`` (dirty -> clean) replaced
# ``lore finish``. Their tests moved to test_session_records.py / test_flush.py.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# permission-log.py
# ---------------------------------------------------------------------------

class TestPermissionLog:
    def test_permission_log_hook_not_present(self):
        """permission-log.py was removed from the hooks directory.

        The PreToolUse / permission-logging hook was removed as part of the
        subsystems→areas rename cleanup. This test asserts the removal is
        intentional so any re-addition of a permission-log hook is a deliberate
        change, not an accidental resurrection.
        """
        assert not (HOOKS_DIR / "permission-log.py").exists(), (
            "permission-log.py must not be present — it was removed with the "
            "PreToolUse hook. Re-adding it requires updating hooks.json too."
        )


# ---------------------------------------------------------------------------
# hooks.json registration (Slice 1, lore-agent-interface: zero push hooks)
# ---------------------------------------------------------------------------

class TestHooksJson:
    def test_no_post_tool_use_harvest_entry(self):
        """hooks.json must carry no PostToolUse entry — harvest hook is retired.

        lore is fully pull: zero push hooks remain.
        """
        data = json.loads((HOOKS_DIR / "hooks.json").read_text())
        hooks = data.get("hooks", {})
        assert "PostToolUse" not in hooks, (
            "hooks.json must NOT register PostToolUse — harvest hook was deleted "
            "(lore-agent-interface Slice 1)"
        )

    def test_zero_push_hooks(self):
        """hooks.json registers zero push hooks (lore is fully pull)."""
        data = json.loads((HOOKS_DIR / "hooks.json").read_text())
        hooks = data.get("hooks", {})
        assert hooks == {}, (
            f"hooks.json must have empty hooks dict, got keys: {list(hooks.keys())}"
        )

    def test_smoke_files_deleted(self):
        assert not (HOOKS_DIR / "session_smoke.py").exists()
        assert not (HOOKS_DIR / "_shared_smoke.py").exists()

    def test_harvest_candidates_hook_file_deleted(self):
        """harvest-candidates.py must not be present — it was deleted in Slice 1."""
        assert not (HOOKS_DIR / "harvest-candidates.py").exists(), (
            "harvest-candidates.py must be removed — lore installs zero push hooks."
        )


# ---------------------------------------------------------------------------
# M1: sessions.py nested-name collision
# session_note_path / all_session_notes_for_worktree must not let worktree
# 'foo' match '…-super-foo.md'.
# ---------------------------------------------------------------------------

def _seed_note(sessions_dir: Path, stamp: str, worktree: str) -> Path:
    """Write a minimal session note with correct filename and worktree frontmatter."""
    p = sessions_dir / f"{stamp}-{worktree}.md"
    p.write_text(
        f"---\ntype: session\nworktree: {worktree}\nstatus: active\n---\n\n# Session\n"
    )
    return p


class TestSessionNoteNestedNameCollision:
    def test_session_note_path_no_false_match_on_super_prefix(self, tmp_path):
        """session_note_path('foo') must not return a 'super-foo' note."""
        vault = _make_vault(tmp_path)
        sessions_dir = vault / "sessions"
        _seed_note(sessions_dir, "2026-06-02-1000", "super-foo")
        foo = _seed_note(sessions_dir, "2026-06-01-1000", "foo")

        s = load_sessions()
        result = s.session_note_path(vault, "foo")
        assert result == foo

    def test_session_note_path_returns_none_when_only_prefix_note_exists(self, tmp_path):
        """If only 'super-foo' exists, looking for 'foo' returns None."""
        vault = _make_vault(tmp_path)
        sessions_dir = vault / "sessions"
        _seed_note(sessions_dir, "2026-06-02-1000", "super-foo")

        s = load_sessions()
        result = s.session_note_path(vault, "foo")
        assert result is None

    def test_all_notes_for_worktree_no_false_match_on_super_prefix(self, tmp_path):
        """all_session_notes_for_worktree('foo') excludes 'super-foo' notes."""
        vault = _make_vault(tmp_path)
        sessions_dir = vault / "sessions"
        _seed_note(sessions_dir, "2026-06-02-1000", "super-foo")
        foo = _seed_note(sessions_dir, "2026-06-01-1000", "foo")

        s = load_sessions()
        results = s.all_session_notes_for_worktree(vault, "foo")
        assert results == [foo]

    def test_all_notes_for_worktree_empty_when_only_prefix_note_exists(self, tmp_path):
        """all_session_notes_for_worktree('foo') returns [] if only super-foo exists."""
        vault = _make_vault(tmp_path)
        sessions_dir = vault / "sessions"
        _seed_note(sessions_dir, "2026-06-02-1000", "super-foo")

        s = load_sessions()
        results = s.all_session_notes_for_worktree(vault, "foo")
        assert results == []


# ---------------------------------------------------------------------------
# M2: commit_vault must not sweep unrelated dirty vault files
