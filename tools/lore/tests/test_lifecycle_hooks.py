"""Tests for session lifecycle hooks.

Covers (TDD — written before the hooks):
- sessions.ensure_session_note: creates a note with valid `session` frontmatter
  and the five required body headings; resumes a note modified inside the window.
- permission-log.py: appends an entry.

The SessionStart hook and WorktreeRemove hook were deleted;
their test coverage was removed here accordingly (lore is fully pull — orientation
lives in agent-rules).

The PostToolUse harvest-candidates hook was
deleted; lore installs zero push hooks. harvest-candidates.py is gone and
hooks.json carries no PostToolUse entry.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
HOOKS_DIR = PLUGIN_ROOT / "hooks"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


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
# hooks.json registration (zero push hooks)
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
# The legacy plural-``sessions/`` finders (sessions.py
# session_note_path / all_session_notes_for_worktree / sweep_orphan_skeletons)
# were retired with the module — no production caller remained after capture
# moved to the singular ``session/`` record. The nested-name-collision
# guard those finders carried is now structural: the singular resolver does an
# exact-stem lookup (``session/<key>.md``), so worktree ``foo`` can never match
# ``super-foo`` by construction (covered in test_session_note_resolution.py).
# ---------------------------------------------------------------------------
