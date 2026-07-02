"""Tests for session lifecycle hooks.

Covers (TDD — written before the hooks):
- sessions.ensure_session_note: creates a note with valid `session` frontmatter
  and the five required body headings; resumes a note modified inside the window.
- permission-log.py: appends an entry.

lore installs zero push hooks: there is no SessionStart, WorktreeRemove, or
PostToolUse harvest-candidates hook (lore is fully pull — orientation lives in
agent-rules). harvest-candidates.py is absent and hooks.json carries no
PostToolUse entry.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
HOOKS_DIR = PLUGIN_ROOT / "hooks"


# ---------------------------------------------------------------------------
# Session capture lives in singular indexed ``session/`` records and ``lore
# flush`` (dirty -> clean), not a frontmatter-note CREATE/finalize lifecycle.
# Those tests live in test_session_records.py / test_flush.py.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# hooks.json registration (zero push hooks)
# ---------------------------------------------------------------------------

class TestHooksJson:
    def test_zero_push_hooks(self):
        """hooks.json registers zero push hooks (lore is fully pull)."""
        data = json.loads((HOOKS_DIR / "hooks.json").read_text())
        hooks = data.get("hooks", {})
        assert hooks == {}, (
            f"hooks.json must have empty hooks dict, got keys: {list(hooks.keys())}"
        )


# ---------------------------------------------------------------------------
# There are no legacy plural-``sessions/`` finders (sessions.py
# session_note_path / all_session_notes_for_worktree / sweep_orphan_skeletons):
# capture lives in the singular ``session/`` record, with no production caller.
# The nested-name-collision
# guard those finders carried is now structural: the singular resolver does an
# exact-stem lookup (``session/<key>.md``), so worktree ``foo`` can never match
# ``super-foo`` by construction (covered in test_session_note_resolution.py).
# ---------------------------------------------------------------------------
