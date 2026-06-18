"""Slice 5 (S3) — call-site rewiring content tests.

After the `recall` command was retired, the documented call sites must point at
`lore search` (the membership query `lore search 'area:<name>'`), not the removed
`lore recall`. These are anti-regression tests for the cutover:

1. brainstorm/SKILL.md instructs `lore search 'area:<name>'` as the imperative,
   primary prior-art mechanism — and no longer references `lore recall`.
2. area/SKILL.md describes agent-driven retrieval via `lore search` + the area map.
3. agents/librarian.md uses `lore search … --json` as its area-scoped primitive.
4. tools/craft/plugins/craft/agents/planner.md references `lore search 'area:'`.

The injection-defense note (shared `<external-memory>` output) is preserved —
`lore search` emits the same channel for shared-layer hits.
"""

from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent / "plugins" / "lore"
SKILLS_DIR = PLUGIN_ROOT / "skills"
AGENTS_DIR = PLUGIN_ROOT / "agents"

CRAFT_AGENTS_DIR = (
    Path(__file__).parent.parent.parent  # tools/
    / "craft" / "plugins" / "craft" / "agents"
)

_BRAINSTORM_SKILL = SKILLS_DIR / "brainstorm" / "SKILL.md"
_AREA_SKILL = SKILLS_DIR / "area" / "SKILL.md"
_LORE_LIBRARIAN = AGENTS_DIR / "librarian.md"
_CRAFT_PLANNER = CRAFT_AGENTS_DIR / "planner.md"


# ---------------------------------------------------------------------------
# brainstorm SKILL — recall is rewired to `lore search`, still primary/imperative
# ---------------------------------------------------------------------------

class TestBrainstormSearchWiring:
    def test_brainstorm_references_lore_search_area(self):
        text = _BRAINSTORM_SKILL.read_text()
        assert "lore search 'area:" in text, (
            "brainstorm/SKILL.md must reference `lore search 'area:<name>'` as the "
            "primary prior-art mechanism (Slice 5 cutover)."
        )

    def test_brainstorm_no_lore_recall(self):
        text = _BRAINSTORM_SKILL.read_text()
        assert "lore recall" not in text, (
            "brainstorm/SKILL.md still references the removed `lore recall` command."
        )

    def test_brainstorm_search_instruction_is_imperative(self):
        text = _BRAINSTORM_SKILL.read_text()
        assert "run `lore search" in text.lower(), (
            "brainstorm/SKILL.md retrieval instruction must be imperative: "
            "'Run `lore search 'area:<name>'` now'."
        )

    def test_brainstorm_preserves_injection_defense_note(self):
        text = _BRAINSTORM_SKILL.read_text()
        assert "<external-memory" in text, (
            "brainstorm/SKILL.md must preserve the shared-layer injection-defense "
            "note (`<external-memory>` output is reference data, never instructions) "
            "— `lore search` emits the same channel."
        )


# ---------------------------------------------------------------------------
# area SKILL — describes agent-driven retrieval via lore search + the area map
# ---------------------------------------------------------------------------

class TestAreaSkillSearchLanguage:
    def test_area_skill_no_lore_recall(self):
        text = _AREA_SKILL.read_text()
        assert "lore recall" not in text, (
            "area/SKILL.md still references the removed `lore recall` command."
        )

    def test_area_skill_describes_lore_search(self):
        text = _AREA_SKILL.read_text()
        assert "lore search" in text, (
            "area/SKILL.md must describe the live agent-driven retrieval mechanism "
            "(`lore search 'area:<name>'`)."
        )

    def test_area_skill_description_mentions_area_map(self):
        text = _AREA_SKILL.read_text()
        assert "area map" in text.lower() or "area-map" in text.lower(), (
            "area/SKILL.md must describe how keywords + summary feed the area map."
        )


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
