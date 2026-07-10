"""Tests for subagent-file wiring in compose_plan (name-based selection).

Contract:
  - a selected subagent name -> a CopyOp for agents/<name>.md (a file, not a dir).
  - apply_plan lands the file under dest/agents/.
  - unselected subagents never appear in the plan.
  - apply_plan produces real files, never symlinks.
"""

from pathlib import Path

from trailhead.capabilities import load_manifest
from trailhead.compose import apply_plan, compose_plan

_REPO_ROOT = Path(__file__).parent.parent.parent
_CRAFT_MANIFEST = _REPO_ROOT / "tools" / "craft" / "capabilities.toml"
_LORE_MANIFEST = _REPO_ROOT / "tools" / "lore" / "capabilities.toml"


def _agent_dests(plan, dest: Path) -> set[str]:
    return {
        str(op.dest.relative_to(dest))
        for op in plan.ops
        if str(op.dest.relative_to(dest)).startswith("agents/")
    }


class TestSubagentSelection:
    def test_selected_subagents_are_file_ops(self, tmp_path):
        m = load_manifest(_CRAFT_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"planner": None, "architect": None}, {}, dest)
        for op in plan.ops:
            if str(op.dest.relative_to(dest)).startswith("agents/"):
                assert op.src.is_file()
        assert _agent_dests(plan, dest) == {"agents/planner.md", "agents/architect.md"}

    def test_unselected_subagents_absent(self, tmp_path):
        m = load_manifest(_CRAFT_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"planner": None}, {}, dest)
        assert "agents/advocate.md" not in _agent_dests(plan, dest)

    def test_empty_selection_has_no_agents(self, tmp_path):
        m = load_manifest(_CRAFT_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {}, {}, dest)
        assert _agent_dests(plan, dest) == set()


class TestSubagentApply:
    def test_subagent_lands_and_content_preserved(self, tmp_path):
        m = load_manifest(_LORE_MANIFEST)
        dest = tmp_path / "dest"
        apply_plan(compose_plan(m, {"librarian": None}, {}, dest))
        landed = dest / "agents" / "librarian.md"
        assert landed.is_file()
        assert landed.read_text() == (m.plugin_root / "agents" / "librarian.md").read_text()

    def test_no_symlinks_in_tree(self, tmp_path):
        m = load_manifest(_CRAFT_MANIFEST)
        dest = tmp_path / "dest"
        plan = compose_plan(m, {"planner": None, "doc-finder": None}, {"plan": None}, dest)
        apply_plan(plan)
        for path in dest.rglob("*"):
            assert not path.is_symlink()
