"""P1-C tests: action-guards and tool-notes blocks in SessionStart context.

All fixtures use SYNTHETIC vocabulary (synth-action, synth-tool, synth-guard,
synth-collab, etc.) per the public-repo fixture discipline axiom.

Test contract (all must fail before the implementation, pass after):
- Fixture vault with collaboration/ notes carrying actions: → output contains
  the aggregated action-guards section.
- Fixture tools/*.md with name:/summary: → output contains the tool-notes block.
- Empty collaboration/dead-ends/tools/ → neither block appears (no empty headings).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import load_script


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_sessions():
    return load_script("sessions")


def _make_vault(tmp_path: Path) -> Path:
    """Create a minimal vault with all expected top-level directories."""
    vault = tmp_path / "vault"
    for d in ("subsystems", "deferred", "dead-ends", "lessons", "sessions",
              "collaboration", "tools"):
        (vault / d).mkdir(parents=True)
    return vault


def _write_collab_note(vault: Path, name: str, actions: list[str]) -> Path:
    """Write a collaboration note with an actions: block-style list."""
    p = vault / "collaboration" / f"{name}.md"
    action_lines = "\n".join(f"  - {a}" for a in actions)
    p.write_text(
        f"---\n"
        f"type: collaboration\n"
        f"status: active\n"
        f"actions:\n"
        f"{action_lines}\n"
        f"---\n\n"
        f"# {name}\n\nSynthetic collaboration note.\n"
    )
    return p


def _write_dead_end_note(vault: Path, name: str, actions: list[str]) -> Path:
    """Write a dead-end note with an actions: block-style list."""
    p = vault / "dead-ends" / f"{name}.md"
    action_lines = "\n".join(f"  - {a}" for a in actions)
    p.write_text(
        f"---\n"
        f"type: dead-end\n"
        f"status: active\n"
        f"actions:\n"
        f"{action_lines}\n"
        f"---\n\n"
        f"# {name}\n\nSynthetic dead-end note.\n"
    )
    return p


def _write_tool_note(vault: Path, stem: str, name: str, summary: str) -> Path:
    """Write a tools/ note with name: and summary: frontmatter."""
    p = vault / "tools" / f"{stem}.md"
    p.write_text(
        f"---\n"
        f"name: {name}\n"
        f"summary: {summary}\n"
        f"---\n\n"
        f"# {name}\n\nSynthetic tool note.\n"
    )
    return p


def _render_index(vault: Path) -> str:
    """Call render_vault_index with a minimal set of arguments."""
    sessions = _load_sessions()
    return sessions.render_vault_index(
        vault=vault,
        worktree_name="synth-worktree",
        project="synth-project",
        session_note=None,
        session_created=False,
    )


# ---------------------------------------------------------------------------
# build_action_index / render_action_guards
# ---------------------------------------------------------------------------

class TestBuildActionIndex:
    def test_empty_collaboration_returns_empty_index(self, tmp_path):
        """Empty collaboration/ and absent dead-ends/ → empty index dict."""
        vault = _make_vault(tmp_path)
        sessions = _load_sessions()
        result = sessions.build_action_index(vault)
        assert result == {}

    def test_single_collaboration_note_single_action(self, tmp_path):
        """One collaboration note with one action → index maps action → counts."""
        vault = _make_vault(tmp_path)
        _write_collab_note(vault, "synth-collab-a", ["synth-action"])
        sessions = _load_sessions()
        result = sessions.build_action_index(vault)
        assert "synth-action" in result
        assert result["synth-action"]["collaboration"] == 1
        assert result["synth-action"]["dead_ends"] == 0

    def test_collaboration_note_multiple_actions(self, tmp_path):
        """One note with two actions → both appear in the index."""
        vault = _make_vault(tmp_path)
        _write_collab_note(vault, "synth-collab-b", ["synth-action-x", "synth-action-y"])
        sessions = _load_sessions()
        result = sessions.build_action_index(vault)
        assert "synth-action-x" in result
        assert "synth-action-y" in result

    def test_dead_end_note_populates_dead_ends_bucket(self, tmp_path):
        """Dead-end note with action → dead_ends bucket incremented."""
        vault = _make_vault(tmp_path)
        _write_dead_end_note(vault, "synth-dead-a", ["synth-action"])
        sessions = _load_sessions()
        result = sessions.build_action_index(vault)
        assert result["synth-action"]["dead_ends"] == 1
        assert result["synth-action"]["collaboration"] == 0

    def test_same_action_in_both_dirs_accumulates(self, tmp_path):
        """Same action in collab + dead-end → both counts > 0."""
        vault = _make_vault(tmp_path)
        _write_collab_note(vault, "synth-collab-c", ["synth-shared-action"])
        _write_dead_end_note(vault, "synth-dead-b", ["synth-shared-action"])
        sessions = _load_sessions()
        result = sessions.build_action_index(vault)
        assert result["synth-shared-action"]["collaboration"] == 1
        assert result["synth-shared-action"]["dead_ends"] == 1

    def test_graduated_collaboration_note_excluded(self, tmp_path):
        """Notes with status: graduated are excluded from the index."""
        vault = _make_vault(tmp_path)
        p = vault / "collaboration" / "synth-graduated.md"
        p.write_text(
            "---\n"
            "type: collaboration\n"
            "status: graduated\n"
            "actions:\n"
            "  - synth-action-graduated\n"
            "---\n\nGraduated note.\n"
        )
        sessions = _load_sessions()
        result = sessions.build_action_index(vault)
        assert "synth-action-graduated" not in result

    def test_obsolete_collaboration_note_excluded(self, tmp_path):
        """Notes with status: obsolete are excluded from the index."""
        vault = _make_vault(tmp_path)
        p = vault / "collaboration" / "synth-obsolete.md"
        p.write_text(
            "---\n"
            "type: collaboration\n"
            "status: obsolete\n"
            "actions:\n"
            "  - synth-action-obsolete\n"
            "---\n\nObsolete note.\n"
        )
        sessions = _load_sessions()
        result = sessions.build_action_index(vault)
        assert "synth-action-obsolete" not in result

    def test_note_without_actions_key_is_ignored(self, tmp_path):
        """Notes with no actions: field are silently skipped."""
        vault = _make_vault(tmp_path)
        p = vault / "collaboration" / "synth-no-actions.md"
        p.write_text(
            "---\n"
            "type: collaboration\n"
            "status: active\n"
            "---\n\nNo actions key.\n"
        )
        sessions = _load_sessions()
        result = sessions.build_action_index(vault)
        assert result == {}

    def test_absent_collaboration_dir_is_safe(self, tmp_path):
        """Missing collaboration/ dir → empty index, no error."""
        vault = _make_vault(tmp_path)
        (vault / "collaboration").rmdir()
        sessions = _load_sessions()
        result = sessions.build_action_index(vault)
        assert result == {}

    def test_absent_dead_ends_dir_is_safe(self, tmp_path):
        """Missing dead-ends/ dir → only collaboration counted, no error."""
        vault = _make_vault(tmp_path)
        (vault / "dead-ends").rmdir()
        _write_collab_note(vault, "synth-collab-d", ["synth-action-d"])
        sessions = _load_sessions()
        result = sessions.build_action_index(vault)
        assert result["synth-action-d"]["collaboration"] == 1


class TestRenderActionGuards:
    def test_empty_vault_returns_none(self, tmp_path):
        """No collab/dead-end notes → render_action_guards returns None."""
        vault = _make_vault(tmp_path)
        sessions = _load_sessions()
        assert sessions.render_action_guards(vault) is None

    def test_returns_string_when_guards_exist(self, tmp_path):
        """Guards present → returns a non-empty string."""
        vault = _make_vault(tmp_path)
        _write_collab_note(vault, "synth-collab-e", ["synth-action-e"])
        sessions = _load_sessions()
        result = sessions.render_action_guards(vault)
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    def test_output_contains_action_name(self, tmp_path):
        """The rendered string contains the action name in bold."""
        vault = _make_vault(tmp_path)
        _write_collab_note(vault, "synth-collab-f", ["synth-action-f"])
        sessions = _load_sessions()
        result = sessions.render_action_guards(vault)
        assert "synth-action-f" in result

    def test_output_has_heading(self, tmp_path):
        """The rendered block has the expected heading."""
        vault = _make_vault(tmp_path)
        _write_collab_note(vault, "synth-collab-g", ["synth-action-g"])
        sessions = _load_sessions()
        result = sessions.render_action_guards(vault)
        assert "### Action guards" in result

    def test_collaboration_count_mentioned(self, tmp_path):
        """The rendered string mentions collaboration count when > 0."""
        vault = _make_vault(tmp_path)
        _write_collab_note(vault, "synth-collab-h", ["synth-action-h"])
        sessions = _load_sessions()
        result = sessions.render_action_guards(vault)
        assert "collaboration" in result

    def test_dead_end_count_mentioned(self, tmp_path):
        """The rendered string mentions dead-end count when > 0."""
        vault = _make_vault(tmp_path)
        _write_dead_end_note(vault, "synth-dead-c", ["synth-action-h"])
        sessions = _load_sessions()
        result = sessions.render_action_guards(vault)
        assert "dead-end" in result

    def test_actions_sorted_alphabetically(self, tmp_path):
        """Multiple actions appear in alphabetical order."""
        vault = _make_vault(tmp_path)
        _write_collab_note(vault, "synth-collab-sort", ["synth-zzz", "synth-aaa"])
        sessions = _load_sessions()
        result = sessions.render_action_guards(vault)
        aaa_pos = result.index("synth-aaa")
        zzz_pos = result.index("synth-zzz")
        assert aaa_pos < zzz_pos


# ---------------------------------------------------------------------------
# list_tool_notes / render_tool_notes
# ---------------------------------------------------------------------------

class TestListToolNotes:
    def test_empty_tools_dir_returns_empty_list(self, tmp_path):
        """Empty tools/ dir → list_tool_notes returns []."""
        vault = _make_vault(tmp_path)
        sessions = _load_sessions()
        result = sessions.list_tool_notes(vault)
        assert result == []

    def test_absent_tools_dir_returns_empty_list(self, tmp_path):
        """Absent tools/ dir → list_tool_notes returns []."""
        vault = _make_vault(tmp_path)
        (vault / "tools").rmdir()
        sessions = _load_sessions()
        result = sessions.list_tool_notes(vault)
        assert result == []

    def test_single_tool_note_returned(self, tmp_path):
        """One tool note → returned as (name, summary) tuple."""
        vault = _make_vault(tmp_path)
        _write_tool_note(vault, "synth-tool-a", "synth-tool-alpha", "Does synthetic things")
        sessions = _load_sessions()
        result = sessions.list_tool_notes(vault)
        assert len(result) == 1
        name, summary = result[0]
        assert name == "synth-tool-alpha"
        assert summary == "Does synthetic things"

    def test_multiple_tool_notes_returned(self, tmp_path):
        """Multiple tool notes → all returned."""
        vault = _make_vault(tmp_path)
        _write_tool_note(vault, "synth-tool-b", "synth-beta-tool", "Beta summary")
        _write_tool_note(vault, "synth-tool-c", "synth-gamma-tool", "Gamma summary")
        sessions = _load_sessions()
        result = sessions.list_tool_notes(vault)
        assert len(result) == 2

    def test_readme_is_excluded(self, tmp_path):
        """README.md in tools/ is excluded."""
        vault = _make_vault(tmp_path)
        (vault / "tools" / "README.md").write_text("# Tools\n")
        _write_tool_note(vault, "synth-tool-d", "synth-delta-tool", "Delta summary")
        sessions = _load_sessions()
        result = sessions.list_tool_notes(vault)
        assert len(result) == 1

    def test_fallback_to_stem_when_no_name_frontmatter(self, tmp_path):
        """Tool note without name: uses filename stem as fallback."""
        vault = _make_vault(tmp_path)
        p = vault / "tools" / "synth-no-name-tool.md"
        p.write_text("---\nsummary: Some summary\n---\n\nBody.\n")
        sessions = _load_sessions()
        result = sessions.list_tool_notes(vault)
        assert len(result) == 1
        name, summary = result[0]
        assert name == "synth-no-name-tool"
        assert summary == "Some summary"

    def test_empty_summary_when_no_summary_frontmatter(self, tmp_path):
        """Tool note without summary: returns empty string for summary."""
        vault = _make_vault(tmp_path)
        p = vault / "tools" / "synth-nosummary.md"
        p.write_text("---\nname: synth-nosummary-tool\n---\n\nBody.\n")
        sessions = _load_sessions()
        result = sessions.list_tool_notes(vault)
        assert len(result) == 1
        name, summary = result[0]
        assert name == "synth-nosummary-tool"
        assert summary == ""


class TestRenderToolNotes:
    def test_empty_tools_returns_none(self, tmp_path):
        """No tool notes → render_tool_notes returns None."""
        vault = _make_vault(tmp_path)
        sessions = _load_sessions()
        assert sessions.render_tool_notes(vault) is None

    def test_returns_string_when_tools_exist(self, tmp_path):
        """Tools present → returns non-empty string."""
        vault = _make_vault(tmp_path)
        _write_tool_note(vault, "synth-tool-e", "synth-epsilon-tool", "Epsilon summary")
        sessions = _load_sessions()
        result = sessions.render_tool_notes(vault)
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    def test_output_contains_tool_name(self, tmp_path):
        """The rendered string contains the tool name."""
        vault = _make_vault(tmp_path)
        _write_tool_note(vault, "synth-tool-f", "synth-zeta-tool", "Zeta summary")
        sessions = _load_sessions()
        result = sessions.render_tool_notes(vault)
        assert "synth-zeta-tool" in result

    def test_output_contains_summary(self, tmp_path):
        """The rendered string contains the summary text."""
        vault = _make_vault(tmp_path)
        _write_tool_note(vault, "synth-tool-g", "synth-eta-tool", "Eta synthetic summary text")
        sessions = _load_sessions()
        result = sessions.render_tool_notes(vault)
        assert "Eta synthetic summary text" in result

    def test_output_has_heading(self, tmp_path):
        """The rendered block has a heading."""
        vault = _make_vault(tmp_path)
        _write_tool_note(vault, "synth-tool-h", "synth-theta-tool", "Theta summary")
        sessions = _load_sessions()
        result = sessions.render_tool_notes(vault)
        assert "### Tool notes" in result

    def test_tool_without_summary_renders_without_dash(self, tmp_path):
        """Tool with empty summary renders without the ' — <summary>' part on its line."""
        vault = _make_vault(tmp_path)
        p = vault / "tools" / "synth-bare-tool.md"
        p.write_text("---\nname: synth-bare-tool-name\n---\n\nBody.\n")
        sessions = _load_sessions()
        result = sessions.render_tool_notes(vault)
        assert "synth-bare-tool-name" in result
        # The tool line itself (starts with "- **`...`**") must not have " — <summary>"
        tool_line = next(
            (line for line in result.splitlines() if "synth-bare-tool-name" in line),
            None,
        )
        assert tool_line is not None
        assert " — " not in tool_line


# ---------------------------------------------------------------------------
# render_vault_index integration: action-guards + tool-notes appear in output
# ---------------------------------------------------------------------------

class TestRenderVaultIndexIntegration:
    def test_empty_vault_has_no_action_guards_heading(self, tmp_path):
        """Empty vault → no Action guards heading in render_vault_index output."""
        vault = _make_vault(tmp_path)
        result = _render_index(vault)
        assert "Action guards" not in result

    def test_empty_vault_has_no_tool_notes_heading(self, tmp_path):
        """Empty vault → no Tool notes heading in render_vault_index output."""
        vault = _make_vault(tmp_path)
        result = _render_index(vault)
        assert "Tool notes" not in result

    def test_collaboration_notes_trigger_action_guards_block(self, tmp_path):
        """Collaboration notes with actions → Action guards block in output."""
        vault = _make_vault(tmp_path)
        _write_collab_note(vault, "synth-int-collab", ["synth-int-action"])
        result = _render_index(vault)
        assert "Action guards" in result
        assert "synth-int-action" in result

    def test_tool_notes_trigger_tool_notes_block(self, tmp_path):
        """Tool notes → Tool notes block in output."""
        vault = _make_vault(tmp_path)
        _write_tool_note(vault, "synth-int-tool", "synth-int-tool-name", "Integration tool summary")
        result = _render_index(vault)
        assert "Tool notes" in result
        assert "synth-int-tool-name" in result

    def test_both_blocks_present_when_both_populated(self, tmp_path):
        """Both collab notes + tool notes → both blocks appear."""
        vault = _make_vault(tmp_path)
        _write_collab_note(vault, "synth-both-collab", ["synth-both-action"])
        _write_tool_note(vault, "synth-both-tool", "synth-both-tool-name", "Both summary")
        result = _render_index(vault)
        assert "Action guards" in result
        assert "Tool notes" in result

    def test_dead_end_actions_appear_in_output(self, tmp_path):
        """Dead-end notes with actions → those actions appear in output."""
        vault = _make_vault(tmp_path)
        _write_dead_end_note(vault, "synth-int-dead", ["synth-dead-int-action"])
        result = _render_index(vault)
        assert "synth-dead-int-action" in result

    def test_block_style_actions_parsed_correctly(self, tmp_path):
        """Block-style actions: list (post-P1-A) renders correctly."""
        vault = _make_vault(tmp_path)
        # This verifies P1-A's block-style parsing feeds into P1-C correctly
        _write_collab_note(vault, "synth-block-collab", ["synth-block-action"])
        sessions = _load_sessions()
        index = sessions.build_action_index(vault)
        assert "synth-block-action" in index
