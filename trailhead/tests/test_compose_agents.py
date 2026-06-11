"""Tests for agent-file wiring in compose_plan (Slice 3 extension).

TDD: written BEFORE implementation. Tests must fail first (agents not wired),
then pass after compose.py is extended to wire agent files.

Contract:
  - compose_plan includes CopyOp entries for each agent file declared in
    selected capabilities (files, not dirs; under dest/agents/).
  - apply_plan lands agent files under dest/agents/.
  - D-F confinement rejects an agent path that escapes plugin_root.
  - An escaping agent path in the manifest raises ConfineError at load time,
    so compose_plan never sees it (same posture as skills).
  - B-5: a minimal lore subset WITHOUT shared-vaults has no shared-vaults
    agents in the tree; no camp/forge dest exists when only lore is selected.
  - S-2: apply_plan(plan, mode="copy") always — no symlinks in the composed tree.
"""

import os
from pathlib import Path

import pytest

from trailhead.capabilities import ConfineError, load_manifest
from trailhead.compose import CopyOp, Plan, apply_plan, compose_plan

_REPO_ROOT = Path(__file__).parent.parent.parent
_LORE_MANIFEST = _REPO_ROOT / "tools" / "lore" / "capabilities.toml"
_FORGE_MANIFEST = _REPO_ROOT / "tools" / "forge" / "capabilities.toml"
_CAMP_MANIFEST = _REPO_ROOT / "tools" / "camp" / "capabilities.toml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "capabilities.toml"
    p.write_text(content)
    return p


def _make_plugin_root(tmp_path: Path, tool_name: str) -> Path:
    plugin_root = tmp_path / "plugins" / tool_name
    plugin_root.mkdir(parents=True)
    claude_plugin = plugin_root / ".claude-plugin"
    claude_plugin.mkdir()
    (claude_plugin / "plugin.json").write_text('{"name": "' + tool_name + '"}')
    return plugin_root


def _make_agent_file(plugin_root: Path, agent_rel: str) -> Path:
    """Create an agent file at plugin_root / agent_rel."""
    p = plugin_root / agent_rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nname: {p.stem}\n---\n# {p.stem}")
    return p


def _make_skill_dir(plugin_root: Path, skill: str) -> Path:
    d = plugin_root / skill
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"# {skill}")
    return d


# ---------------------------------------------------------------------------
# T-A1: compose_plan includes agent-file CopyOps for selected capabilities
# ---------------------------------------------------------------------------


class TestAgentCopyOpsIncluded:
    def test_forge_planning_agents_in_plan(self, tmp_path):
        """forge planning capability declares planner.md + architect.md agents;
        both must appear in compose_plan output."""
        m = load_manifest(_FORGE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"planning"}, dest)
        dest_paths = {op.dest for op in plan.ops}
        for agent in m.capabilities["planning"]["agents"]:
            assert dest / agent in dest_paths, (
                f"agent {agent!r} missing from plan ops"
            )

    def test_agent_ops_are_file_ops_not_dir_ops(self, tmp_path):
        """Agent CopyOps must point at real .md files (not directories)."""
        m = load_manifest(_FORGE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"planning"}, dest)
        agent_dests = {
            dest / agent
            for agent in m.capabilities["planning"]["agents"]
        }
        for op in plan.ops:
            if op.dest in agent_dests:
                assert op.src.is_file(), (
                    f"agent src {op.src} is not a file"
                )

    def test_lore_recall_lore_librarian_in_plan(self, tmp_path):
        """lore recall capability declares agents/lore-librarian.md; must be in plan."""
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"recall"}, dest)
        dest_paths = {op.dest for op in plan.ops}
        assert dest / "agents/lore-librarian.md" in dest_paths

    def test_lore_capture_has_no_agents(self, tmp_path):
        """lore capture has agents=[] — no agent CopyOps from that capability."""
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"capture"}, dest)
        dest_paths = {op.dest for op in plan.ops}
        # capture has no agents declared
        for agent in m.capabilities["capture"]["agents"]:
            assert dest / agent in dest_paths  # vacuously true for empty list

        # Confirm no agent path is wired (agents dir would be under dest/agents/)
        agent_ops = [
            op for op in plan.ops
            if "agents/" in str(op.dest.relative_to(dest))
        ]
        assert len(agent_ops) == 0, (
            f"capture produced unexpected agent ops: {agent_ops}"
        )

    def test_unselected_capability_agents_absent(self, tmp_path):
        """Agents from an unselected capability must NOT appear in the plan."""
        m = load_manifest(_FORGE_MANIFEST)
        dest = tmp_path / "dest"
        # Select planning only — circle agents (council-*.md) must not appear
        plan = compose_plan(m, {"planning"}, dest)
        dest_paths = {op.dest for op in plan.ops}
        for agent in m.capabilities["circle"]["agents"]:
            assert dest / agent not in dest_paths, (
                f"unselected circle agent {agent!r} leaked into plan"
            )

    def test_empty_selection_no_agent_ops(self, tmp_path):
        """Empty selection → no agent CopyOps (agents only come from selected caps)."""
        m = load_manifest(_FORGE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, set(), dest)
        agent_ops = [
            op for op in plan.ops
            if "agents/" in str(op.dest.relative_to(dest))
        ]
        assert len(agent_ops) == 0


# ---------------------------------------------------------------------------
# T-A2: apply_plan lands agent files under dest/agents/
# ---------------------------------------------------------------------------


class TestAgentApplyLanding:
    def test_agent_files_land_under_dest_agents(self, tmp_path):
        """After apply_plan, agent files exist under dest/agents/."""
        m = load_manifest(_FORGE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"planning"}, dest)
        apply_plan(plan, mode="copy")
        for agent in m.capabilities["planning"]["agents"]:
            agent_path = dest / agent
            assert agent_path.exists(), (
                f"agent {agent!r} missing from dest after apply_plan"
            )
            assert agent_path.is_file(), (
                f"agent path {agent_path} is not a file"
            )

    def test_lore_librarian_lands_after_apply(self, tmp_path):
        """lore recall → agents/lore-librarian.md lands in dest."""
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"recall"}, dest)
        apply_plan(plan, mode="copy")
        agent_path = dest / "agents" / "lore-librarian.md"
        assert agent_path.exists()
        assert agent_path.is_file()

    def test_agent_content_preserved(self, tmp_path):
        """Agent file content must be intact after apply_plan."""
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"recall"}, dest)
        apply_plan(plan, mode="copy")
        src_agent = m.plugin_root / "agents" / "lore-librarian.md"
        dest_agent = dest / "agents" / "lore-librarian.md"
        assert dest_agent.read_text() == src_agent.read_text()

    def test_s2_no_symlinks_in_composed_tree(self, tmp_path):
        """S-2: apply_plan(mode='copy') must produce real files, not symlinks."""
        m = load_manifest(_FORGE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"planning", "helpers"}, dest)
        apply_plan(plan, mode="copy")
        # Walk entire dest — no symlinks allowed
        for path in dest.rglob("*"):
            assert not path.is_symlink(), (
                f"symlink found in composed tree: {path}"
            )


# ---------------------------------------------------------------------------
# T-A3: D-F confinement for agent paths
# ---------------------------------------------------------------------------


class TestAgentConfinement:
    def test_escaping_agent_path_raises_confine_error_at_load(self, tmp_path):
        """An agent path with ../escape raises ConfineError at load_manifest time."""
        content = """
[tool]
name = "badtool"
base = []

[capabilities.bad]
description = "escaping agent"
skills = []
agents = ["../../../etc/shadow.md"]
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ConfineError):
            load_manifest(manifest_path)

    def test_absolute_agent_path_raises_confine_error_at_load(self, tmp_path):
        """An absolute path in agents raises ConfineError at load_manifest time."""
        content = """
[tool]
name = "badtool"
base = []

[capabilities.bad]
description = "absolute agent injection"
skills = []
agents = ["/etc/passwd.md"]
"""
        manifest_path = _write_manifest(tmp_path, content)
        with pytest.raises(ConfineError):
            load_manifest(manifest_path)

    def test_valid_agent_path_confined_stays_inside_plugin_root(self, tmp_path):
        """A valid agent path must resolve to inside plugin_root."""
        plugin_root = _make_plugin_root(tmp_path, "mytool")
        _make_agent_file(plugin_root, "agents/helper.md")

        content = """
[tool]
name = "mytool"
base = []
validate = false

[capabilities.cap]
description = "has an agent"
skills = []
agents = ["agents/helper.md"]
"""
        manifest_path = _write_manifest(tmp_path, content)
        m = load_manifest(manifest_path)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"cap"}, dest)
        plugin_root_resolved = m.plugin_root.resolve()
        for op in plan.ops:
            if op.dest == dest / "agents/helper.md":
                assert op.src.is_relative_to(plugin_root_resolved)


# ---------------------------------------------------------------------------
# T-A4: B-5 subset enforcement — minimal lore has no shared-vaults agents,
#        no camp dest, no forge dest
# ---------------------------------------------------------------------------


class TestSubsetEnforcement:
    def test_minimal_lore_plan_has_no_shared_vaults_agents(self, tmp_path):
        """Composing lore without shared-vaults: shared-vaults agents absent."""
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        # shared-vaults has agents=[] anyway, but assert no agents leak from unselected caps
        plan = compose_plan(m, {"capture", "recall", "sessions"}, dest)
        dest_paths = {op.dest for op in plan.ops}
        for agent in m.capabilities["shared-vaults"]["agents"]:
            assert dest / agent not in dest_paths, (
                f"shared-vaults agent {agent!r} leaked into minimal lore plan"
            )

    def test_minimal_lore_apply_no_shared_vaults_content(self, tmp_path):
        """After apply_plan for minimal lore, no shared-vaults skill dirs exist."""
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"capture", "recall", "sessions"}, dest)
        apply_plan(plan, mode="copy")
        for skill in m.capabilities["shared-vaults"]["skills"]:
            assert not (dest / skill).exists(), (
                f"shared-vaults skill {skill!r} present despite not being selected"
            )

    def test_helpers_agents_all_present_when_selected(self, tmp_path):
        """forge helpers capability has many agents — all must appear in plan."""
        m = load_manifest(_FORGE_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"helpers"}, dest)
        dest_paths = {op.dest for op in plan.ops}
        for agent in m.capabilities["helpers"]["agents"]:
            assert dest / agent in dest_paths, (
                f"helpers agent {agent!r} missing from plan"
            )

    def test_plan_count_includes_agents(self, tmp_path):
        """compose_plan for recall adds 1 agent file op; count must reflect this."""
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"

        # Empty selection: always-on only (no agents)
        plan_empty = compose_plan(m, set(), dest)
        count_empty = len(plan_empty.ops)

        # recall: adds 2 skill dirs + 1 agent file
        plan_recall = compose_plan(m, {"recall"}, tmp_path / "dest2")
        count_recall = len(plan_recall.ops)

        recall_skills = len(m.capabilities["recall"]["skills"])
        recall_agents = len(m.capabilities["recall"]["agents"])
        assert count_recall == count_empty + recall_skills + recall_agents, (
            f"expected {count_empty + recall_skills + recall_agents} ops for recall, "
            f"got {count_recall}"
        )
