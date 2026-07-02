"""Tests for workspace doc (CLAUDE.md + AGENT.md) and workspace SessionStart hook.

Test contract (all must RED before implementation, GREEN after):

1. workspace CLAUDE.md + AGENT.md written at bring-up:
   a. Both files exist at the workspace root after bring_up_workspace.
   b. Each file contains a verbatim, invocable command table with exact strings
      'camp activate <member>', 'camp status', 'camp setup' (exact-string match).
   c. Doc contains the member list (each member name).
   d. Doc contains "inert until" or equivalent phrasing.
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
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_SCRIPTS_DIR = _PLUGIN_DIR / "scripts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_group_config(name, members, *, branch_pattern="worktree-{slug}", harness=None):
    cfg = {"group": {"name": name}, "members": members, "branch_pattern": branch_pattern}
    if harness is not None:
        cfg["harness"] = harness
    return cfg


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

    def test_agent_md_not_written_by_default(self, tmp_path: Path):
        """write_workspace_doc (claude default, no [harness]) does NOT write AGENT.md."""
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

        assert not (ws_dir / "AGENT.md").exists(), (
            "AGENT.md should NOT be written when no [harness] doc_files is configured"
        )


# ---------------------------------------------------------------------------
# Test 1b: verbatim command table (exact-string match)
# ---------------------------------------------------------------------------


class TestWorkspaceDocCommandTable:
    def _get_claude_md(self, tmp_path: Path) -> str:
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
        return (ws_dir / "CLAUDE.md").read_text()

    def test_claude_md_contains_camp_activate_exact(self, tmp_path: Path):
        """CLAUDE.md contains the exact string 'camp activate <member>'."""
        content = self._get_claude_md(tmp_path)
        assert "camp activate <member>" in content, (
            f"Expected 'camp activate <member>' in CLAUDE.md, not found.\nContent:\n{content}"
        )

    def test_claude_md_contains_camp_status_exact(self, tmp_path: Path):
        """CLAUDE.md contains the exact string 'camp status'."""
        content = self._get_claude_md(tmp_path)
        assert "camp status" in content, (
            f"Expected 'camp status' in CLAUDE.md, not found.\nContent:\n{content}"
        )

    def test_claude_md_contains_camp_setup_exact(self, tmp_path: Path):
        """CLAUDE.md contains the exact string 'camp setup' (no --retry flag)."""
        content = self._get_claude_md(tmp_path)
        assert "camp setup" in content, (
            f"Expected 'camp setup' in CLAUDE.md, not found.\nContent:\n{content}"
        )

    def test_claude_md_does_not_contain_camp_setup_retry(self, tmp_path: Path):
        """CLAUDE.md must NOT contain 'camp setup --retry' — the flag was removed."""
        content = self._get_claude_md(tmp_path)
        assert "camp setup --retry" not in content, (
            f"Found 'camp setup --retry' in CLAUDE.md — this flag was removed.\nContent:\n{content}"
        )

    def test_configured_agents_md_contains_camp_activate_exact(self, tmp_path: Path):
        """When doc_files=[AGENTS.md], that file contains 'camp activate <member>'."""
        from workspace_doc import write_workspace_doc

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        group = _make_group_config(
            "mygroup",
            [{"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []}],
            harness={"doc_files": ["AGENTS.md"]},
        )
        write_workspace_doc(ws_dir, group, "feat-x")
        content = (ws_dir / "AGENTS.md").read_text()
        assert "camp activate <member>" in content, (
            f"Expected 'camp activate <member>' in AGENTS.md, not found.\nContent:\n{content}"
        )

    def test_claude_md_has_no_stale_verbs(self, tmp_path: Path):
        """Generated CLAUDE.md names 'camp activate' and no removed 'camp enter'/'camp ai'."""
        content = self._get_claude_md(tmp_path)
        assert "camp activate" in content, (
            f"Generated doc must name 'camp activate'.\nContent:\n{content}"
        )
        assert "camp enter" not in content, (
            f"Generated doc must not name the removed 'camp enter'.\nContent:\n{content}"
        )
        assert "camp ai" not in content, (
            f"Generated doc must not name the removed 'camp ai'.\nContent:\n{content}"
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

    def test_configured_agents_md_contains_all_member_names(self, tmp_path: Path):
        """When doc_files=[AGENTS.md], that file lists all member names."""
        from workspace_doc import write_workspace_doc

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        group = _make_group_config(
            "mygroup",
            [
                {"name": "alpha", "repo_root": str(tmp_path / "alpha"), "bootstrap": []},
                {"name": "beta", "repo_root": str(tmp_path / "beta"), "bootstrap": []},
            ],
            harness={"doc_files": ["AGENTS.md"]},
        )
        write_workspace_doc(ws_dir, group, "feat-x")

        content = (ws_dir / "AGENTS.md").read_text()
        for name in ("alpha", "beta"):
            assert name in content, f"Member {name!r} missing from AGENTS.md"


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
            f"Expected 'inert until' or equivalent in CLAUDE.md.\nContent:\n{content}"
        )

    def test_setup_may_be_in_flight(self, tmp_path: Path):
        """CLAUDE.md documents that setup may be in flight."""
        content = self._write_and_read(tmp_path)
        # Accept several phrasings indicating provisioning in flight
        flight_signals = [
            "in flight",
            "in-flight",
            "background",
            "provisioning",
            "pending",
        ]
        assert any(sig in content for sig in flight_signals), (
            f"Expected provisioning-in-flight guidance in CLAUDE.md.\nContent:\n{content}"
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

        assert first == second, "CLAUDE.md content changed on second write (not idempotent)"

    def test_configured_agents_md_no_duplication_on_second_write(self, tmp_path: Path):
        """Writing twice with doc_files=[AGENTS.md] produces identical AGENTS.md."""
        from workspace_doc import write_workspace_doc

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        group = _make_group_config(
            "mygroup",
            [{"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []}],
            harness={"doc_files": ["AGENTS.md"]},
        )
        write_workspace_doc(ws_dir, group, "feat-x")
        first = (ws_dir / "AGENTS.md").read_text()

        write_workspace_doc(ws_dir, group, "feat-x")
        second = (ws_dir / "AGENTS.md").read_text()

        assert first == second, "AGENTS.md content changed on second write (not idempotent)"

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
        from camp.harness.hooks_writer import write_workspace_hooks

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        write_workspace_hooks(ws_dir, "/path/to/camp")

        assert (ws_dir / ".claude" / "settings.json").is_file()

    def test_workspace_settings_has_session_start_hook(self, tmp_path: Path):
        """workspace .claude/settings.json carries SessionStart→camp setup --status."""
        from camp.harness.hooks_writer import write_workspace_hooks

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        camp_bin = "/path/to/camp"
        write_workspace_hooks(ws_dir, camp_bin)

        data = json.loads((ws_dir / ".claude" / "settings.json").read_text())
        hooks = data.get("hooks", {})
        ss_hooks = hooks.get("SessionStart", [])
        commands = [h.get("command", "") for entry in ss_hooks for h in entry.get("hooks", [])]
        # The command uses the ${CAMP_BIN:-<bin>} form; check for setup --status suffix
        assert any("setup --status" in cmd for cmd in commands), (
            f"Expected 'setup --status' hook in SessionStart, got: {commands}"
        )

    def test_workspace_session_start_command_exact(self, tmp_path: Path):
        """The SessionStart command is exactly the expected shell-expandable form."""
        from camp.harness.hooks_writer import write_workspace_hooks

        camp_bin = "/usr/local/bin/camp"
        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        write_workspace_hooks(ws_dir, camp_bin)

        data = json.loads((ws_dir / ".claude" / "settings.json").read_text())
        hooks = data.get("hooks", {})
        ss_hooks = hooks.get("SessionStart", [])
        commands = [h.get("command", "") for entry in ss_hooks for h in entry.get("hooks", [])]
        expected = f"${{CAMP_BIN:-{camp_bin}}} setup --status"
        assert expected in commands, (
            f"Expected exact command {expected!r} in SessionStart, got: {commands}"
        )

    def test_workspace_hooks_idempotent(self, tmp_path: Path):
        """Re-running write_workspace_hooks adds NO duplicate entries."""
        from camp.harness.hooks_writer import write_workspace_hooks

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        camp_bin = "/usr/local/bin/camp"

        write_workspace_hooks(ws_dir, camp_bin)
        write_workspace_hooks(ws_dir, camp_bin)

        data = json.loads((ws_dir / ".claude" / "settings.json").read_text())
        hooks = data.get("hooks", {})
        ss_hooks = hooks.get("SessionStart", [])
        commands = [h.get("command", "") for entry in ss_hooks for h in entry.get("hooks", [])]
        expected = f"${{CAMP_BIN:-{camp_bin}}} setup --status"
        assert commands.count(expected) == 1, f"Duplicate hook entries after re-run: {commands}"

    def test_workspace_hooks_preserves_existing_keys(self, tmp_path: Path):
        """Existing unrelated keys in workspace settings.json are preserved."""
        from camp.harness.hooks_writer import write_workspace_hooks

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
        from camp.harness.hooks_writer import write_workspace_hooks

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        member_dir = tmp_path / "member"
        member_dir.mkdir()

        write_workspace_hooks(ws_dir, "/path/to/camp")

        assert (ws_dir / ".claude" / "settings.json").is_file()
        assert not (member_dir / ".claude" / "settings.json").is_file()


# ---------------------------------------------------------------------------
# PostToolUse → camp inject --drain workspace hook
# ---------------------------------------------------------------------------


class TestWorkspaceInjectHook:
    def _commands(self, data: dict, event: str) -> list[str]:
        return [
            h.get("command", "")
            for entry in data.get("hooks", {}).get(event, [])
            for h in entry.get("hooks", [])
        ]

    def _matchers(self, data: dict, event: str) -> list[str]:
        return [entry.get("matcher", "") for entry in data.get("hooks", {}).get(event, [])]

    def test_inject_hook_written(self, tmp_path: Path):
        """write_workspace_inject_hook writes a PostToolUse → inject --drain hook."""
        from camp.harness.hooks_writer import write_workspace_inject_hook

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        camp_bin = "/usr/local/bin/camp"
        write_workspace_inject_hook(ws_dir, camp_bin)

        data = json.loads((ws_dir / ".claude" / "settings.json").read_text())
        commands = self._commands(data, "PostToolUse")
        expected = f"${{CAMP_BIN:-{camp_bin}}} inject --drain"
        assert expected in commands, f"Expected {expected!r} in PostToolUse, got: {commands}"

    def test_inject_hook_matcher_is_bash(self, tmp_path: Path):
        """The PostToolUse inject hook uses the Bash matcher."""
        from camp.harness.hooks_writer import write_workspace_inject_hook

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        write_workspace_inject_hook(ws_dir, "/usr/local/bin/camp")

        data = json.loads((ws_dir / ".claude" / "settings.json").read_text())
        assert "Bash" in self._matchers(data, "PostToolUse")

    def test_inject_hook_idempotent(self, tmp_path: Path):
        """Re-running write_workspace_inject_hook adds NO duplicate entries."""
        from camp.harness.hooks_writer import write_workspace_inject_hook

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        camp_bin = "/usr/local/bin/camp"
        write_workspace_inject_hook(ws_dir, camp_bin)
        write_workspace_inject_hook(ws_dir, camp_bin)

        data = json.loads((ws_dir / ".claude" / "settings.json").read_text())
        commands = self._commands(data, "PostToolUse")
        expected = f"${{CAMP_BIN:-{camp_bin}}} inject --drain"
        assert commands.count(expected) == 1, f"Duplicate inject hook: {commands}"

    def test_inject_hook_preserves_session_start(self, tmp_path: Path):
        """Writing the inject hook does not clobber an existing SessionStart hook."""
        from camp.harness.hooks_writer import write_workspace_hooks, write_workspace_inject_hook

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        camp_bin = "/usr/local/bin/camp"
        write_workspace_hooks(ws_dir, camp_bin)
        write_workspace_inject_hook(ws_dir, camp_bin)

        data = json.loads((ws_dir / ".claude" / "settings.json").read_text())
        assert any("setup --status" in c for c in self._commands(data, "SessionStart"))
        assert any("inject --drain" in c for c in self._commands(data, "PostToolUse"))


class TestHasInjectDrainHook:
    """BUG 5: detecting whether the PostToolUse inject --drain hook is installed."""

    def test_true_when_drain_hook_installed(self, tmp_path: Path):
        from camp.harness.hooks_writer import has_inject_drain_hook, write_workspace_inject_hook

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        write_workspace_inject_hook(ws_dir, "/usr/local/bin/camp")

        assert has_inject_drain_hook(ws_dir) is True

    def test_false_when_no_settings_file(self, tmp_path: Path):
        from camp.harness.hooks_writer import has_inject_drain_hook

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()

        assert has_inject_drain_hook(ws_dir) is False

    def test_false_when_only_session_start_hook(self, tmp_path: Path):
        from camp.harness.hooks_writer import has_inject_drain_hook, write_workspace_hooks

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        write_workspace_hooks(ws_dir, "/usr/local/bin/camp")

        assert has_inject_drain_hook(ws_dir) is False


# ---------------------------------------------------------------------------
# Test 4: bring_up_workspace integration
# ---------------------------------------------------------------------------


class TestBringUpWorkspaceIntegration:
    def test_bring_up_creates_claude_md(self, tmp_path: Path):
        """bring_up_workspace writes CLAUDE.md at the workspace root."""
        from camp.provision.provision import bring_up_workspace

        env = _camp_state_env(tmp_path)
        group = _make_group_config(
            "mygroup",
            [{"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []}],
        )

        import unittest.mock as mock

        with mock.patch("camp.provision.provision.spawn_detached_provisioner"):
            bring_up_workspace(group, "feat-doc", env=env)

        from camp.group.resolve import central_state_dir

        ws_dir = central_state_dir("mygroup", env=env) / "worktrees" / "feat-doc"
        assert (ws_dir / "CLAUDE.md").is_file(), "CLAUDE.md missing after bring_up_workspace"

    def test_bring_up_does_not_create_agent_md_by_default(self, tmp_path: Path):
        """bring_up_workspace (claude default) does NOT write AGENT.md."""
        from camp.provision.provision import bring_up_workspace

        env = _camp_state_env(tmp_path)
        group = _make_group_config(
            "mygroup",
            [{"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []}],
        )

        import unittest.mock as mock

        with mock.patch("camp.provision.provision.spawn_detached_provisioner"):
            bring_up_workspace(group, "feat-doc2", env=env)

        from camp.group.resolve import central_state_dir

        ws_dir = central_state_dir("mygroup", env=env) / "worktrees" / "feat-doc2"
        assert not (ws_dir / "AGENT.md").exists(), (
            "AGENT.md should NOT be written by default (no doc_files configured)"
        )

    def test_bring_up_creates_workspace_settings(self, tmp_path: Path):
        """bring_up_workspace writes workspace .claude/settings.json with SessionStart hook."""
        from camp.provision.provision import bring_up_workspace

        env = _camp_state_env(tmp_path)
        group = _make_group_config(
            "mygroup",
            [{"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []}],
        )

        import unittest.mock as mock

        with mock.patch("camp.provision.provision.spawn_detached_provisioner"):
            bring_up_workspace(group, "feat-doc3", env=env)

        from camp.group.resolve import central_state_dir

        ws_dir = central_state_dir("mygroup", env=env) / "worktrees" / "feat-doc3"
        settings_path = ws_dir / ".claude" / "settings.json"
        assert settings_path.is_file(), "workspace .claude/settings.json missing"

        data = json.loads(settings_path.read_text())
        hooks = data.get("hooks", {})
        ss_hooks = hooks.get("SessionStart", [])
        commands = [h.get("command", "") for entry in ss_hooks for h in entry.get("hooks", [])]
        assert any("setup --status" in cmd for cmd in commands), (
            f"SessionStart hook not found. Settings: {data}"
        )

    def test_bring_up_claude_md_has_member_names(self, tmp_path: Path):
        """bring_up_workspace CLAUDE.md embeds member names."""
        from camp.provision.provision import bring_up_workspace

        env = _camp_state_env(tmp_path)
        group = _make_group_config(
            "mygroup",
            [
                {"name": "alpha", "repo_root": str(tmp_path / "alpha"), "bootstrap": []},
                {"name": "beta", "repo_root": str(tmp_path / "beta"), "bootstrap": []},
            ],
        )

        import unittest.mock as mock

        with mock.patch("camp.provision.provision.spawn_detached_provisioner"):
            bring_up_workspace(group, "feat-doc4", env=env)

        from camp.group.resolve import central_state_dir

        ws_dir = central_state_dir("mygroup", env=env) / "worktrees" / "feat-doc4"
        content = (ws_dir / "CLAUDE.md").read_text()
        assert "alpha" in content and "beta" in content

    def test_bring_up_idempotent_docs(self, tmp_path: Path):
        """Calling bring_up_workspace twice produces identical docs (no duplication)."""
        from camp.provision.provision import bring_up_workspace

        env = _camp_state_env(tmp_path)
        group = _make_group_config(
            "mygroup",
            [{"name": "repo_a", "repo_root": str(tmp_path / "repo_a"), "bootstrap": []}],
        )

        import unittest.mock as mock

        with mock.patch("camp.provision.provision.spawn_detached_provisioner"):
            bring_up_workspace(group, "feat-idem", env=env)

        from camp.group.resolve import central_state_dir

        ws_dir = central_state_dir("mygroup", env=env) / "worktrees" / "feat-idem"
        first_claude = (ws_dir / "CLAUDE.md").read_text()

        with mock.patch("camp.provision.provision.spawn_detached_provisioner"):
            bring_up_workspace(group, "feat-idem", env=env)

        second_claude = (ws_dir / "CLAUDE.md").read_text()
        assert first_claude == second_claude, "CLAUDE.md duplicated on second bring-up"


# ---------------------------------------------------------------------------
# doc_files (resolve_harness_profile) + Members-line removal
# ---------------------------------------------------------------------------


class TestResolveDocFiles:
    """Unit tests for resolve_harness_profile(...).doc_files."""

    def test_no_harness_returns_claude_md(self):
        """No [harness] block → doc_files is ['CLAUDE.md']."""
        from camp.harness.profile import resolve_harness_profile

        group = {"group": {"name": "g"}, "members": []}
        assert resolve_harness_profile(group).doc_files == ["CLAUDE.md"]

    def test_harness_without_doc_files_returns_claude_md(self):
        """[harness] block without doc_files → ['CLAUDE.md']."""
        from camp.harness.profile import resolve_harness_profile

        group = {
            "group": {"name": "g"},
            "members": [],
            "harness": {
                "binary": "claude",
                "cwd": "{workspace}",
            },
        }
        assert resolve_harness_profile(group).doc_files == ["CLAUDE.md"]

    def test_configured_doc_files_returned(self):
        """Configured doc_files is returned as-is."""
        from camp.harness.profile import resolve_harness_profile

        group = {
            "group": {"name": "g"},
            "members": [],
            "harness": {"doc_files": ["AGENTS.md"]},
        }
        assert resolve_harness_profile(group).doc_files == ["AGENTS.md"]

    def test_multiple_doc_files_returned(self):
        """Multiple configured doc_files are all returned."""
        from camp.harness.profile import resolve_harness_profile

        group = {
            "group": {"name": "g"},
            "members": [],
            "harness": {"doc_files": ["AGENTS.md", "CLAUDE.md"]},
        }
        assert resolve_harness_profile(group).doc_files == ["AGENTS.md", "CLAUDE.md"]


class TestWriteWorkspaceDocFiles:
    """Tests for write_workspace_doc file-selection behavior."""

    def test_default_writes_only_claude_md(self, tmp_path: Path):
        """No [harness] config → only CLAUDE.md written, AGENT.md not written."""
        from workspace_doc import write_workspace_doc

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        group = _make_group_config(
            "g",
            [{"name": "r", "repo_root": "/tmp/r", "bootstrap": []}],
        )
        write_workspace_doc(ws_dir, group, "s")

        assert (ws_dir / "CLAUDE.md").is_file()
        assert not (ws_dir / "AGENT.md").exists()
        assert not (ws_dir / "AGENTS.md").exists()

    def test_configured_agents_md_writes_agents_md_only(self, tmp_path: Path):
        """doc_files=['AGENTS.md'] → AGENTS.md written, CLAUDE.md not written."""
        from workspace_doc import write_workspace_doc

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        group = _make_group_config(
            "g",
            [{"name": "r", "repo_root": "/tmp/r", "bootstrap": []}],
            harness={"doc_files": ["AGENTS.md"]},
        )
        write_workspace_doc(ws_dir, group, "s")

        assert (ws_dir / "AGENTS.md").is_file()
        assert not (ws_dir / "CLAUDE.md").exists()

    def test_configured_both_writes_both(self, tmp_path: Path):
        """doc_files=['AGENTS.md','CLAUDE.md'] → both files written."""
        from workspace_doc import write_workspace_doc

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        group = _make_group_config(
            "g",
            [{"name": "r", "repo_root": "/tmp/r", "bootstrap": []}],
            harness={"doc_files": ["AGENTS.md", "CLAUDE.md"]},
        )
        write_workspace_doc(ws_dir, group, "s")

        assert (ws_dir / "AGENTS.md").is_file()
        assert (ws_dir / "CLAUDE.md").is_file()

    def test_each_doc_has_same_content(self, tmp_path: Path):
        """All written doc files have identical rendered content."""
        from workspace_doc import write_workspace_doc

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        group = _make_group_config(
            "g",
            [{"name": "r", "repo_root": "/tmp/r", "bootstrap": []}],
            harness={"doc_files": ["AGENTS.md", "CLAUDE.md"]},
        )
        write_workspace_doc(ws_dir, group, "s")

        agents_content = (ws_dir / "AGENTS.md").read_text()
        claude_content = (ws_dir / "CLAUDE.md").read_text()
        assert agents_content == claude_content


class TestRenderedDocMembersLine:
    """The trailing 'Members: ...' line must be removed."""

    def _get_content(self, tmp_path: Path, member_names=None) -> str:
        from workspace_doc import write_workspace_doc

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        members = [
            {"name": n, "repo_root": f"/tmp/{n}", "bootstrap": []}
            for n in (member_names or ["alpha", "beta"])
        ]
        group = _make_group_config("g", members)
        write_workspace_doc(ws_dir, group, "slug")
        return (ws_dir / "CLAUDE.md").read_text()

    def test_no_bare_members_line(self, tmp_path: Path):
        """Rendered doc must NOT contain a line starting with 'Members:'."""
        content = self._get_content(tmp_path)
        for line in content.splitlines():
            assert not line.startswith("Members:"), (
                f"Found redundant 'Members:' line in rendered doc: {line!r}\n"
                f"Full content:\n{content}"
            )

    def test_members_section_heading_retained(self, tmp_path: Path):
        """Rendered doc retains the '## Members' section heading."""
        content = self._get_content(tmp_path)
        assert "## Members" in content, (
            f"'## Members' heading missing from rendered doc.\nContent:\n{content}"
        )

    def test_member_names_still_listed(self, tmp_path: Path):
        """Member names are still present in the ## Members block."""
        content = self._get_content(tmp_path, member_names=["alpha", "beta"])
        assert "alpha" in content
        assert "beta" in content


class TestGroupConfigDocFiles:
    """group_config validates doc_files when present."""

    def _toml_with_doc_files(self, doc_files_toml: str) -> str:
        return f"""\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[harness]
binary = "claude"
doc_files = {doc_files_toml}
"""

    def test_valid_doc_files_loads(self, tmp_path: Path):
        """A valid doc_files list is accepted and returned."""
        from camp.group.config import load_group

        f = tmp_path / "g.toml"
        f.write_text(self._toml_with_doc_files('["AGENTS.md"]'))
        cfg = load_group(f)
        assert cfg["harness"]["doc_files"] == ["AGENTS.md"]

    def test_doc_files_multiple_accepted(self, tmp_path: Path):
        """Multiple doc_files entries are accepted."""
        from camp.group.config import load_group

        f = tmp_path / "g.toml"
        f.write_text(self._toml_with_doc_files('["AGENTS.md", "CLAUDE.md"]'))
        cfg = load_group(f)
        assert cfg["harness"]["doc_files"] == ["AGENTS.md", "CLAUDE.md"]

    def test_empty_list_raises(self, tmp_path: Path):
        """An empty doc_files list → GroupConfigError."""
        from camp.group.config import GroupConfigError, load_group

        f = tmp_path / "g.toml"
        f.write_text(self._toml_with_doc_files("[]"))
        with pytest.raises(GroupConfigError) as exc_info:
            load_group(f)
        assert "doc_files" in str(exc_info.value)

    def test_whitespace_token_raises(self, tmp_path: Path):
        """A whitespace-only token in doc_files → GroupConfigError."""
        from camp.group.config import GroupConfigError, load_group

        f = tmp_path / "g.toml"
        f.write_text(self._toml_with_doc_files('["  "]'))
        with pytest.raises(GroupConfigError) as exc_info:
            load_group(f)
        assert "doc_files" in str(exc_info.value)

    def test_non_string_token_raises(self, tmp_path: Path):
        """A non-string token in doc_files → GroupConfigError."""
        from camp.group.config import GroupConfigError, load_group

        f = tmp_path / "g.toml"
        f.write_text(self._toml_with_doc_files("[42]"))
        with pytest.raises(GroupConfigError) as exc_info:
            load_group(f)
        assert "doc_files" in str(exc_info.value)

    def test_not_a_list_raises(self, tmp_path: Path):
        """doc_files as a string (not a list) → GroupConfigError."""
        from camp.group.config import GroupConfigError, load_group

        f = tmp_path / "g.toml"
        f.write_text(self._toml_with_doc_files('"AGENTS.md"'))
        with pytest.raises(GroupConfigError) as exc_info:
            load_group(f)
        assert "doc_files" in str(exc_info.value)

    def test_absent_doc_files_returns_none(self, tmp_path: Path):
        """When doc_files is absent from [harness], it's not present in the parsed harness."""
        from camp.group.config import load_group

        toml = """\
[group]
name = "testgroup"

[[members]]
name = "myrepo"
repo_root = "/tmp/myrepo"

[harness]
binary = "claude"
"""
        f = tmp_path / "g.toml"
        f.write_text(toml)
        cfg = load_group(f)
        assert "doc_files" not in cfg["harness"]
