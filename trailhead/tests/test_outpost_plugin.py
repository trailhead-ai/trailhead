"""Contract tests for the wired ``tools/outpost`` plugin's anatomy.

``tools/outpost`` is skill-only: no python package, no agents, no always-on
``base``. Its skills are discovered on disk, so the anatomy that matters is
what the *loader* and the *composer* make of the plugin — not which files are
sitting in the tree. Every test here runs one of them and asserts on its
output.
"""

from __future__ import annotations

from pathlib import Path

from trailhead.capabilities import load_manifest
from trailhead.compose import apply_plan, compose_plan

_REPO_ROOT = Path(__file__).parent.parent.parent
_CAPABILITIES = _REPO_ROOT / "tools" / "outpost" / "capabilities.toml"


def _manifest():
    return load_manifest(_CAPABILITIES)


class TestManifestLoads:
    """What the capabilities loader makes of the plugin."""

    def test_publish_site_is_a_discovered_skill(self):
        assert _manifest().skills.get("publish-site") == "skills/publish-site"

    def test_declared_ruleset_resolves_to_readable_content(self):
        m = _manifest()
        assert m.ruleset_path().read_text(encoding="utf-8").strip() != ""


class TestComposition:
    """What the composer emits when the plugin is wired."""

    def test_composed_install_carries_a_plugin_manifest_naming_outpost(self, tmp_path):
        """`plugin.json` lands in the composed dest naming the tool — wire.py
        reads it there to decide outpost is wired, so a composition that drops
        it (or names something else) wires nothing."""
        import json

        apply_plan(compose_plan(_manifest(), None, {"publish-site": None}, tmp_path))
        data = json.loads((tmp_path / ".claude-plugin" / "plugin.json").read_text())
        assert data["name"] == "outpost"
        assert data.get("description", "").strip() != ""

    def test_plan_ships_the_selected_skill(self, tmp_path):
        plan = compose_plan(_manifest(), None, {"publish-site": None}, tmp_path)
        dests = {str(op.dest.relative_to(tmp_path)) for op in plan.ops}
        assert any(d.startswith("skills/publish-site") for d in dests)

    def test_unselected_plugin_composes_nothing(self, tmp_path):
        """Selecting no skills yields no skill ops — selection is what drives
        the plan, not what happens to exist under the plugin root."""
        plan = compose_plan(_manifest(), None, {}, tmp_path)
        dests = {str(op.dest.relative_to(tmp_path)) for op in plan.ops}
        assert not any(d.startswith("skills/") for d in dests)
