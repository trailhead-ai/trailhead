"""Slice 5 (S3) — call-site rewiring content tests.

After the `recall` command was retired, the documented call sites must point at
`lore search` (the membership query `lore search 'area:<name>'`), not the removed
`lore recall`. These are anti-regression tests for the cutover:

1. agents/librarian.md uses `lore search … --json` as its area-scoped primitive.
2. tools/craft/plugins/craft/agents/planner.md references `lore search 'area:'`.

Note: area/SKILL.md was deleted in S6 Slice 2 (replaced by `lore record`/CLI
surface), so area-skill wiring tests are no longer applicable. brainstorm/SKILL.md
moved to the craft plugin in S6 Slice 3 — its `lore search` wiring tests moved
with it to tools/craft/tests/test_brainstorm_generic.py.

The injection-defense note (shared `<external-memory>` output) is preserved —
`lore search` emits the same channel for shared-layer hits.
"""

from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent / "plugins" / "lore"
AGENTS_DIR = PLUGIN_ROOT / "agents"

CRAFT_AGENTS_DIR = (
    Path(__file__).parent.parent.parent  # tools/
    / "craft" / "plugins" / "craft" / "agents"
)

_LORE_LIBRARIAN = AGENTS_DIR / "librarian.md"
_CRAFT_PLANNER = CRAFT_AGENTS_DIR / "planner.md"


# ---------------------------------------------------------------------------
# librarian agent — area-scoped retrieval primitive is lore search --json
# ---------------------------------------------------------------------------

class TestLoreLibrarianSearchPrimitive:
    def test_lore_librarian_references_lore_search(self):
        text = _LORE_LIBRARIAN.read_text()
        assert "lore search" in text, (
            "agents/librarian.md must reference `lore search` as the area-scoped "
            "retrieval primitive (Slice 5)."
        )

    def test_lore_librarian_no_lore_recall(self):
        text = _LORE_LIBRARIAN.read_text()
        assert "lore recall" not in text, (
            "agents/librarian.md still references the removed `lore recall` command."
        )

    def test_lore_librarian_uses_json_flag(self):
        text = _LORE_LIBRARIAN.read_text()
        assert "--json" in text, (
            "agents/librarian.md must document `lore search 'area:<name>' --json` "
            "for programmatic (structured) use."
        )


# ---------------------------------------------------------------------------
# craft planner agent — recall reference rewired to lore search
# ---------------------------------------------------------------------------

class TestCraftPlannerSearchReference:
    def test_craft_planner_references_lore_search(self):
        assert _CRAFT_PLANNER.exists(), (
            f"craft planner.md not found at {_CRAFT_PLANNER}."
        )
        text = _CRAFT_PLANNER.read_text()
        assert "lore search" in text, (
            "craft/agents/planner.md must reference `lore search 'area:<name>'` in "
            "its context-exploration step (Slice 5 cutover)."
        )

    def test_craft_planner_no_lore_recall(self):
        text = _CRAFT_PLANNER.read_text()
        assert "lore recall" not in text, (
            "craft/agents/planner.md still references the removed `lore recall`."
        )
