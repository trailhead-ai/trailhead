"""Tests for Slice 4: workspace doc (CLAUDE.md + AGENT.md) and workspace SessionStart hook.

Test contract (all must RED before implementation, GREEN after):

1. workspace CLAUDE.md + AGENT.md written at bring-up:
   a. Both files exist at the workspace root after bring_up_workspace.
   b. Each file contains a verbatim, invocable command table with exact strings
      'camp enter <member>', 'camp status', 'camp setup --retry' (exact-string match).
   c. Doc contains the member list (each member name).
   d. Doc contains "inert until camp enter" or equivalent phrasing.
   e. Doc contains "setup may be in flight" or equivalent phrasing.

2. Rewrite on re-run is idempotent — no duplication, stable content for stable inputs:
   a. Writing twice produces identical file content (not doubled/appended).
   b. The content is deterministic for the same group + slug inputs.

3. workspace .claude/settings.json SessionStart→`camp setup --status` hook:
   a. Written at the workspace root (not in any member repo).
   b. Carries exactly the hook for 'camp setup --status' in SessionStart.
   c. No duplicate on re-run (mirrors hooks_writer idempotency).
   d. Existing unrelated keys in workspace settings.json are preserved.

4. bring_up_workspace writes both docs + settings (integration):
   a. After bring_up_workspace, CLAUDE.md + AGENT.md exist at workspace root.
   b. After bring_up_workspace, workspace .claude/settings.json carries the
      SessionStart hook.

5. SessionStart relocation — member-repo hook:
   a. The member-repo .claude/settings.json still carries its session-bootstrap hook
      (retained per resolver behavior: member-repo cwd → (group, None) → no-op;
       the hook is inert but harmless; the workspace-dir hook is the live path).
   b. The workspace dir .claude/settings.json is the authoritative SessionStart source
      for a workspace session (covered by test 3).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_group_config(name, members, *, branch_pattern="worktree-{slug}"):
    return {"group": {"name": name}, "members": members, "branch_pattern": branch_pattern}


def _camp_state_env(tmp_path: Path) -> dict[str, str]:
    state_root = tmp_path / "camp-state"
    state_root.mkdir(parents=True, exist_ok=True)
    return {"CAMP_STATE_DIR": str(state_root)}


# ---------------------------------------------------------------------------
# Test 1a: workspace_doc.write_workspace_doc writes both files
# ---------------------------------------------------------------------------


class TestWorkspaceDocFiles:
    def test_claude_md_written(self, tmp_path: Path):
        """write_workspace_doc writes CLAUDE.md at the workspace root."""
        from workspace_doc import write_workspace_doc

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        group = _make_group_config(
            "mygroup",
            [
                {"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []},
                {"name": "repo_b", "repo_root": str(tmp_path / "repo_b"), "bootstrap": []},
            ],
        )
        write_workspace_doc(ws_dir, group, "feat-x")

        assert (ws_dir / "CLAUDE.md").is_file()

    def test_agent_md_written(self, tmp_path: Path):
        """write_workspace_doc writes AGENT.md at the workspace root."""
        from workspace_doc import write_workspace_doc

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        group = _make_group_config(
            "mygroup",
            [
                {"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []},
            ],
        )
        write_workspace_doc(ws_dir, group, "feat-x")

        assert (ws_dir / "AGENT.md").is_file()


# ---------------------------------------------------------------------------
# Test 1b: verbatim command table (exact-string match)
# ---------------------------------------------------------------------------


class TestWorkspaceDocCommandTable:
    def _get_docs(self, tmp_path: Path):
        from workspace_doc import write_workspace_doc

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        group = _make_group_config(
            "mygroup",
            [
                {"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []},
                {"name": "repo_b", "repo_root": str(tmp_path / "repo_b"), "bootstrap": []},
            ],
        )
        write_workspace_doc(ws_dir, group, "feat-x")
        return (ws_dir / "CLAUDE.md").read_text(), (ws_dir / "AGENT.md").read_text()

    def test_claude_md_contains_camp_enter_exact(self, tmp_path: Path):
        """CLAUDE.md contains the exact string 'camp enter <member>'."""
        content, _ = self._get_docs(tmp_path)
        assert "camp enter <member>" in content, (
            f"Expected 'camp enter <member>' in CLAUDE.md, not found.\n"
            f"Content:\n{content}"
        )

    def test_claude_md_contains_camp_status_exact(self, tmp_path: Path):
        """CLAUDE.md contains the exact string 'camp status'."""
        content, _ = self._get_docs(tmp_path)
        assert "camp status" in content, (
            f"Expected 'camp status' in CLAUDE.md, not found.\n"
            f"Content:\n{content}"
        )

    def test_claude_md_contains_camp_setup_retry_exact(self, tmp_path: Path):
        """CLAUDE.md contains the exact string 'camp setup --retry'."""
        content, _ = self._get_docs(tmp_path)
        assert "camp setup --retry" in content, (
            f"Expected 'camp setup --retry' in CLAUDE.md, not found.\n"
            f"Content:\n{content}"
        )

    def test_agent_md_contains_camp_enter_exact(self, tmp_path: Path):
        """AGENT.md contains the exact string 'camp enter <member>'."""
        _, content = self._get_docs(tmp_path)
        assert "camp enter <member>" in content, (
            f"Expected 'camp enter <member>' in AGENT.md, not found.\n"
            f"Content:\n{content}"
        )

    def test_agent_md_contains_camp_status_exact(self, tmp_path: Path):
        """AGENT.md contains the exact string 'camp status'."""
        _, content = self._get_docs(tmp_path)
        assert "camp status" in content, (
            f"Expected 'camp status' in AGENT.md, not found.\n"
            f"Content:\n{content}"
        )

    def test_agent_md_contains_camp_setup_retry_exact(self, tmp_path: Path):
        """AGENT.md contains the exact string 'camp setup --retry'."""
        _, content = self._get_docs(tmp_path)
        assert "camp setup --retry" in content, (
            f"Expected 'camp setup --retry' in AGENT.md, not found.\n"
            f"Content:\n{content}"
        )


# ---------------------------------------------------------------------------
# Test 1c: member list embedded
# ---------------------------------------------------------------------------


class TestWorkspaceDocMemberList:
    def test_claude_md_contains_all_member_names(self, tmp_path: Path):
        """CLAUDE.md lists all member names."""
        from workspace_doc import write_workspace_doc

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        group = _make_group_config(
            "mygroup",
            [
                {"name": "alpha", "repo_root": str(tmp_path / "alpha"), "bootstrap": []},
                {"name": "beta", "repo_root": str(tmp_path / "beta"), "bootstrap": []},
                {"name": "gamma", "repo_root": str(tmp_path / "gamma"), "bootstrap": []},
            ],
        )
        write_workspace_doc(ws_dir, group, "feat-x")

        content = (ws_dir / "CLAUDE.md").read_text()
        for name in ("alpha", "beta", "gamma"):
            assert name in content, f"Member {name!r} missing from CLAUDE.md"

    def test_agent_md_contains_all_member_names(self, tmp_path: Path):
        """AGENT.md lists all member names."""
        from workspace_doc import write_workspace_doc

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        group = _make_group_config(
            "mygroup",
            [
                {"name": "alpha", "repo_root": str(tmp_path / "alpha"), "bootstrap": []},
                {"name": "beta", "repo_root": str(tmp_path / "beta"), "bootstrap": []},
            ],
        )
        write_workspace_doc(ws_dir, group, "feat-x")

        content = (ws_dir / "AGENT.md").read_text()
        for name in ("alpha", "beta"):
            assert name in content, f"Member {name!r} missing from AGENT.md"


# ---------------------------------------------------------------------------
# Test 1d+1e: inert-until-enter + setup-may-be-in-flight guidance
# ---------------------------------------------------------------------------


class TestWorkspaceDocGuidance:
    def _write_and_read(self, tmp_path: Path):
        from workspace_doc import write_workspace_doc

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        group = _make_group_config(
            "mygroup",
            [{"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []}],
        )
        write_workspace_doc(ws_dir, group, "feat-x")
        return (ws_dir / "CLAUDE.md").read_text()

    def test_inert_until_camp_enter(self, tmp_path: Path):
        """CLAUDE.md documents that members are inert until 'camp enter'."""
        content = self._write_and_read(tmp_path)
        # Accept several phrasings: "inert until", "inactive until", "not active until"
        inert_signals = ["inert until", "inactive until", "not active until", "INERT UNTIL"]
        assert any(sig in content for sig in inert_signals), (
            f"Expected 'inert until' or equivalent in CLAUDE.md.\n"
            f"Content:\n{content}"
        )

    def test_setup_may_be_in_flight(self, tmp_path: Path):
        """CLAUDE.md documents that setup may be in flight."""
        content = self._write_and_read(tmp_path)
        # Accept several phrasings indicating provisioning in flight
        flight_signals = [
            "in flight", "in-flight", "background", "provisioning", "pending",
        ]
        assert any(sig in content for sig in flight_signals), (
            f"Expected provisioning-in-flight guidance in CLAUDE.md.\n"
            f"Content:\n{content}"
        )


# ---------------------------------------------------------------------------
# Test 2: idempotent — no duplication, stable content
# ---------------------------------------------------------------------------


class TestWorkspaceDocIdempotent:
    def test_no_duplication_on_second_write(self, tmp_path: Path):
        """Writing twice produces identical CLAUDE.md — no duplication."""
        from workspace_doc import write_workspace_doc

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        group = _make_group_config(
            "mygroup",
            [
                {"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []},
                {"name": "repo_b", "repo_root": str(tmp_path / "repo_b"), "bootstrap": []},
            ],
        )
        write_workspace_doc(ws_dir, group, "feat-x")
        first = (ws_dir / "CLAUDE.md").read_text()

        write_workspace_doc(ws_dir, group, "feat-x")
        second = (ws_dir / "CLAUDE.md").read_text()

        assert first == second, (
            "CLAUDE.md content changed on second write (not idempotent)"
        )

    def test_agent_md_no_duplication_on_second_write(self, tmp_path: Path):
        """Writing twice produces identical AGENT.md — no duplication."""
        from workspace_doc import write_workspace_doc

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        group = _make_group_config(
            "mygroup",
            [{"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []}],
        )
        write_workspace_doc(ws_dir, group, "feat-x")
        first = (ws_dir / "AGENT.md").read_text()

        write_workspace_doc(ws_dir, group, "feat-x")
        second = (ws_dir / "AGENT.md").read_text()

        assert first == second, (
            "AGENT.md content changed on second write (not idempotent)"
        )

    def test_stable_content_for_same_inputs(self, tmp_path: Path):
        """Same group + slug always produces the same content."""
        from workspace_doc import write_workspace_doc

        group = _make_group_config(
            "mygroup",
            [
                {"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []},
            ],
        )

        ws_dir_1 = tmp_path / "ws1"
        ws_dir_1.mkdir()
        write_workspace_doc(ws_dir_1, group, "feat-x")
        content_1 = (ws_dir_1 / "CLAUDE.md").read_text()

        ws_dir_2 = tmp_path / "ws2"
        ws_dir_2.mkdir()
        write_workspace_doc(ws_dir_2, group, "feat-x")
        content_2 = (ws_dir_2 / "CLAUDE.md").read_text()

        assert content_1 == content_2, (
            "CLAUDE.md content differs between two calls with identical inputs"
        )


# ---------------------------------------------------------------------------
# Test 3: workspace .claude/settings.json SessionStart→camp setup --status
# ---------------------------------------------------------------------------


class TestWorkspaceHooks:
    def test_workspace_settings_written(self, tmp_path: Path):
        """write_workspace_hooks writes .claude/settings.json at the workspace root."""
        from hooks_writer import write_workspace_hooks

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        write_workspace_hooks(ws_dir, "/path/to/camp")

        assert (ws_dir / ".claude" / "settings.json").is_file()

    def test_workspace_settings_has_session_start_hook(self, tmp_path: Path):
        """workspace .claude/settings.json carries SessionStart→camp setup --status."""
        from hooks_writer import write_workspace_hooks

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        camp_bin = "/path/to/camp"
        write_workspace_hooks(ws_dir, camp_bin)

        data = json.loads((ws_dir / ".claude" / "settings.json").read_text())
        hooks = data.get("hooks", {})
        ss_hooks = hooks.get("SessionStart", [])
        commands = [
            h.get("command", "")
            for entry in ss_hooks
            for h in entry.get("hooks", [])
        ]
        # The command uses the ${CAMP_BIN:-<bin>} form; check for setup --status suffix
        assert any("setup --status" in cmd for cmd in commands), (
            f"Expected 'setup --status' hook in SessionStart, got: {commands}"
        )

    def test_workspace_session_start_command_exact(self, tmp_path: Path):
        """The SessionStart command is exactly the expected shell-expandable form."""
        from hooks_writer import write_workspace_hooks

        camp_bin = "/usr/local/bin/camp"
        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        write_workspace_hooks(ws_dir, camp_bin)

        data = json.loads((ws_dir / ".claude" / "settings.json").read_text())
        hooks = data.get("hooks", {})
        ss_hooks = hooks.get("SessionStart", [])
        commands = [
            h.get("command", "")
            for entry in ss_hooks
            for h in entry.get("hooks", [])
        ]
        expected = f"${{CAMP_BIN:-{camp_bin}}} setup --status"
        assert expected in commands, (
            f"Expected exact command {expected!r} in SessionStart, got: {commands}"
        )

    def test_workspace_hooks_idempotent(self, tmp_path: Path):
        """Re-running write_workspace_hooks adds NO duplicate entries."""
        from hooks_writer import write_workspace_hooks

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        camp_bin = "/usr/local/bin/camp"

        write_workspace_hooks(ws_dir, camp_bin)
        write_workspace_hooks(ws_dir, camp_bin)

        data = json.loads((ws_dir / ".claude" / "settings.json").read_text())
        hooks = data.get("hooks", {})
        ss_hooks = hooks.get("SessionStart", [])
        commands = [
            h.get("command", "")
            for entry in ss_hooks
            for h in entry.get("hooks", [])
        ]
        expected = f"${{CAMP_BIN:-{camp_bin}}} setup --status"
        assert commands.count(expected) == 1, (
            f"Duplicate hook entries after re-run: {commands}"
        )

    def test_workspace_hooks_preserves_existing_keys(self, tmp_path: Path):
        """Existing unrelated keys in workspace settings.json are preserved."""
        from hooks_writer import write_workspace_hooks

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        claude_dir = ws_dir / ".claude"
        claude_dir.mkdir()
        settings_path = claude_dir / "settings.json"
        existing = {
            "model": "claude-opus-4",
            "permissions": {"allow": ["Bash(git *)"]},
        }
        settings_path.write_text(json.dumps(existing))

        write_workspace_hooks(ws_dir, "/path/to/camp")

        data = json.loads(settings_path.read_text())
        assert data.get("model") == "claude-opus-4"
        assert data.get("permissions") == {"allow": ["Bash(git *)"]}

    def test_workspace_hooks_written_not_in_member_repo(self, tmp_path: Path):
        """The workspace .claude/settings.json is written to the workspace dir, not a member."""
        from hooks_writer import write_workspace_hooks

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        member_dir = tmp_path / "member"
        member_dir.mkdir()

        write_workspace_hooks(ws_dir, "/path/to/camp")

        assert (ws_dir / ".claude" / "settings.json").is_file()
        assert not (member_dir / ".claude" / "settings.json").is_file()


# ---------------------------------------------------------------------------
# Test 4: bring_up_workspace integration
# ---------------------------------------------------------------------------


class TestBringUpWorkspaceIntegration:
    def test_bring_up_creates_claude_md(self, tmp_path: Path):
        """bring_up_workspace writes CLAUDE.md at the workspace root."""
        from provision import bring_up_workspace

        env = _camp_state_env(tmp_path)
        group = _make_group_config(
            "mygroup",
            [{"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []}],
        )

        import unittest.mock as mock
        with mock.patch("provision.spawn_detached_provisioner"):
            bring_up_workspace(group, "feat-doc", env=env)

        from group_resolve import central_state_dir
        ws_dir = central_state_dir("mygroup", env=env) / "worktrees" / "feat-doc"
        assert (ws_dir / "CLAUDE.md").is_file(), "CLAUDE.md missing after bring_up_workspace"

    def test_bring_up_creates_agent_md(self, tmp_path: Path):
        """bring_up_workspace writes AGENT.md at the workspace root."""
        from provision import bring_up_workspace

        env = _camp_state_env(tmp_path)
        group = _make_group_config(
            "mygroup",
            [{"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []}],
        )

        import unittest.mock as mock
        with mock.patch("provision.spawn_detached_provisioner"):
            bring_up_workspace(group, "feat-doc2", env=env)

        from group_resolve import central_state_dir
        ws_dir = central_state_dir("mygroup", env=env) / "worktrees" / "feat-doc2"
        assert (ws_dir / "AGENT.md").is_file(), "AGENT.md missing after bring_up_workspace"

    def test_bring_up_creates_workspace_settings(self, tmp_path: Path):
        """bring_up_workspace writes workspace .claude/settings.json with SessionStart hook."""
        from provision import bring_up_workspace

        env = _camp_state_env(tmp_path)
        group = _make_group_config(
            "mygroup",
            [{"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []}],
        )

        import unittest.mock as mock
        with mock.patch("provision.spawn_detached_provisioner"):
            bring_up_workspace(group, "feat-doc3", env=env)

        from group_resolve import central_state_dir
        ws_dir = central_state_dir("mygroup", env=env) / "worktrees" / "feat-doc3"
        settings_path = ws_dir / ".claude" / "settings.json"
        assert settings_path.is_file(), "workspace .claude/settings.json missing"

        data = json.loads(settings_path.read_text())
        hooks = data.get("hooks", {})
        ss_hooks = hooks.get("SessionStart", [])
        commands = [
            h.get("command", "")
            for entry in ss_hooks
            for h in entry.get("hooks", [])
        ]
        assert any("setup --status" in cmd for cmd in commands), (
            f"SessionStart hook not found. Settings: {data}"
        )

    def test_bring_up_claude_md_has_member_names(self, tmp_path: Path):
        """bring_up_workspace CLAUDE.md embeds member names."""
        from provision import bring_up_workspace

        env = _camp_state_env(tmp_path)
        group = _make_group_config(
            "mygroup",
            [
                {"name": "alpha", "repo_root": str(tmp_path / "alpha"), "bootstrap": []},
                {"name": "beta", "repo_root": str(tmp_path / "beta"), "bootstrap": []},
            ],
        )

        import unittest.mock as mock
        with mock.patch("provision.spawn_detached_provisioner"):
            bring_up_workspace(group, "feat-doc4", env=env)

        from group_resolve import central_state_dir
        ws_dir = central_state_dir("mygroup", env=env) / "worktrees" / "feat-doc4"
        content = (ws_dir / "CLAUDE.md").read_text()
        assert "alpha" in content and "beta" in content

    def test_bring_up_idempotent_docs(self, tmp_path: Path):
        """Calling bring_up_workspace twice produces identical docs (no duplication)."""
        from provision import bring_up_workspace

        env = _camp_state_env(tmp_path)
        group = _make_group_config(
            "mygroup",
            [{"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []}],
        )

        import unittest.mock as mock
        with mock.patch("provision.spawn_detached_provisioner"):
            bring_up_workspace(group, "feat-idem", env=env)

        from group_resolve import central_state_dir
        ws_dir = central_state_dir("mygroup", env=env) / "worktrees" / "feat-idem"
        first_claude = (ws_dir / "CLAUDE.md").read_text()

        with mock.patch("provision.spawn_detached_provisioner"):
            bring_up_workspace(group, "feat-idem", env=env)

        second_claude = (ws_dir / "CLAUDE.md").read_text()
        assert first_claude == second_claude, "CLAUDE.md duplicated on second bring-up"
