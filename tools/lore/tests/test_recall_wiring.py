"""Slice 3 — recall wiring content tests.

Asserts that:
1. brainstorm/SKILL.md has lore recall --areas as the IMPERATIVE, PRIMARY prior-art
   mechanism and no longer instructs manual decisions/ reads as the primary path.
2. area/SKILL.md no longer contains "inactive" / "removed pending" recall language
   and describes agent-driven recall.
3. agents/loremaster.md references `lore recall` with `--json`.
4. tools/forge/plugins/forge/agents/planner.md references `lore recall --areas`.

These are anti-regression tests for D-9 (recall wired as primary, not advisory).
"""
from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent / "plugins" / "lore"
SKILLS_DIR = PLUGIN_ROOT / "skills"
AGENTS_DIR = PLUGIN_ROOT / "agents"

# forge planner lives in the sibling forge plugin tree
# __file__ = tools/lore/tests/test_recall_wiring.py
# parent      = tools/lore/tests/
# parent.parent = tools/lore/
# parent.parent.parent = tools/
FORGE_AGENTS_DIR = (
    Path(__file__).parent.parent.parent  # tools/
    / "forge" / "plugins" / "forge" / "agents"
)

_BRAINSTORM_SKILL = SKILLS_DIR / "brainstorm" / "SKILL.md"
_AREA_SKILL = SKILLS_DIR / "area" / "SKILL.md"
_LORE_LIBRARIAN = AGENTS_DIR / "loremaster.md"
_FORGE_PLANNER = FORGE_AGENTS_DIR / "planner.md"


# ---------------------------------------------------------------------------
# brainstorm SKILL — D-9 anti-regression (recall is PRIMARY, imperative)
# ---------------------------------------------------------------------------

class TestBrainstormRecallWiring:
    def test_brainstorm_references_lore_recall_areas(self):
        """brainstorm/SKILL.md must contain `lore recall --areas` as the primary
        prior-art lookup (D-9 requirement)."""
        text = _BRAINSTORM_SKILL.read_text()
        assert "lore recall --areas" in text, (
            "brainstorm/SKILL.md must reference `lore recall --areas` as the "
            "imperative, primary prior-art mechanism (D-9). "
            "Add an imperative instruction to run `lore recall --areas <names>` "
            "before any manual vault reads."
        )

    def test_brainstorm_recall_instruction_is_imperative(self):
        """The recall instruction in brainstorm/SKILL.md must use imperative
        phrasing (e.g. 'Run `lore recall`' or 'run `lore recall`'), not
        advisory ('you may find it useful to …')."""
        text = _BRAINSTORM_SKILL.read_text()
        # Accept both 'Run `lore recall' and 'run `lore recall' — case-insensitive
        # imperative check. The word "run" + backtick-quoted command is the signal.
        assert "run `lore recall" in text.lower(), (
            "brainstorm/SKILL.md recall instruction must be imperative: "
            "'Run `lore recall --areas …` now' — not advisory phrasing. "
            "D-9: a feature the agent must *remember* to invoke optionally will sit dead."
        )

    def test_brainstorm_no_manual_decisions_read_as_primary(self):
        """brainstorm/SKILL.md must NOT instruct reading decisions/ as the
        *primary* prior-art path. Manual reads are now the fallback only (D-9).

        The old phrasing was a bullet: '- Relevant decisions in `$LORE_VAULT/decisions/`'
        inside the primary 'Pull related prior art' block. After the D-9 fix,
        `lore recall` is primary and manual reads are demoted to fallback only.
        """
        text = _BRAINSTORM_SKILL.read_text()
        # The old primary-path bullet must be gone. We assert that the decisions/
        # directory reference no longer appears as a primary pull bullet.
        # The key diagnostic: if the phrase below is present, the manual-read
        # block is still the primary path (old behavior).
        old_primary_phrasing = "Relevant decisions in `$LORE_VAULT/decisions/`"
        assert old_primary_phrasing not in text, (
            "brainstorm/SKILL.md still instructs manual `decisions/` reads as the "
            "primary prior-art path. Replace the 'Pull related prior art' block "
            "with `lore recall --areas` as the imperative first lookup; demote "
            "manual reads to fallback only (D-9)."
        )

    def test_brainstorm_lore_recall_appears_before_manual_fallback(self):
        """lore recall must appear earlier in the file than any manual-read
        fallback reference, confirming recall is primary (D-9 ordering)."""
        text = _BRAINSTORM_SKILL.read_text()
        recall_pos = text.find("lore recall --areas")
        assert recall_pos != -1, (
            "brainstorm/SKILL.md must contain `lore recall --areas`"
        )
        # The manual fallback keyword (if present) must come AFTER the recall call
        # We look for any surviving manual-fallback language
        fallback_markers = [
            "$LORE_VAULT/dead-ends/",
            "$LORE_VAULT/lessons/",
            "read the relevant notes directly",
        ]
        for marker in fallback_markers:
            pos = text.find(marker)
            if pos != -1:
                assert pos > recall_pos, (
                    f"brainstorm/SKILL.md: manual-read fallback marker {marker!r} "
                    f"appears at position {pos}, BEFORE `lore recall --areas` at "
                    f"{recall_pos}. recall must be primary (first). (D-9)"
                )


# ---------------------------------------------------------------------------
# area SKILL — stale "inactive" language must be corrected
# ---------------------------------------------------------------------------

class TestAreaSkillRecallLanguage:
    def test_area_skill_no_inactive_recall_language(self):
        """area/SKILL.md must NOT say recall is 'inactive' or 'currently inactive'."""
        text = _AREA_SKILL.read_text()
        assert "currently inactive" not in text, (
            "area/SKILL.md still says recall is 'currently inactive'. "
            "Recall shipped in Slice 1; update to describe agent-driven recall."
        )

    def test_area_skill_no_removed_pending_language(self):
        """area/SKILL.md must NOT say the recall path was 'removed pending redesign'."""
        text = _AREA_SKILL.read_text()
        assert "removed pending" not in text, (
            "area/SKILL.md still references 'removed pending a smarter redesign'. "
            "Recall has been redesigned and shipped; update the description."
        )

    def test_area_skill_describes_agent_driven_recall(self):
        """area/SKILL.md must describe the now-live D23 agent-driven recall:
        keywords + summary feed the area map; agent calls `lore recall --areas`."""
        text = _AREA_SKILL.read_text()
        assert "lore recall" in text, (
            "area/SKILL.md must describe the live agent-driven recall mechanism "
            "(`lore recall --areas`). The 'currently inactive' placeholder language "
            "must be replaced with an accurate description of D23 recall."
        )

    def test_area_skill_description_mentions_area_map(self):
        """area/SKILL.md must mention the area map (menu) as the mechanism by
        which keywords + summary are used at runtime."""
        text = _AREA_SKILL.read_text()
        # Either "area map" or "area-map" or similar must appear
        assert "area map" in text.lower() or "area-map" in text.lower(), (
            "area/SKILL.md must describe how keywords + summary feed the area map "
            "(the always-loaded menu the agent uses to match tasks to areas). "
            "Update the stale placeholder with accurate D23 language."
        )


# ---------------------------------------------------------------------------
# loremaster agent — recall primitive
# ---------------------------------------------------------------------------

class TestLoreLibrarianRecallPrimitive:
    def test_lore_librarian_references_lore_recall_json(self):
        """agents/loremaster.md must reference `lore recall` with `--json`
        as its area-scoped retrieval primitive."""
        text = _LORE_LIBRARIAN.read_text()
        assert "lore recall" in text, (
            "agents/loremaster.md must reference `lore recall` as the "
            "area-scoped retrieval primitive (D-9, Slice 3)."
        )

    def test_lore_librarian_uses_json_flag(self):
        """agents/loremaster.md must document `--json` for programmatic use."""
        text = _LORE_LIBRARIAN.read_text()
        assert "--json" in text, (
            "agents/loremaster.md must document `lore recall --areas <names> --json` "
            "for programmatic (structured) use. Add --json to the retrieval primitive."
        )

    def test_lore_librarian_uses_areas_flag(self):
        """agents/loremaster.md must reference `--areas` in the recall call."""
        text = _LORE_LIBRARIAN.read_text()
        assert "--areas" in text, (
            "agents/loremaster.md must reference `lore recall --areas <names>` "
            "as the area-scoped retrieval call."
        )


# ---------------------------------------------------------------------------
# forge planner agent — recall reference
# ---------------------------------------------------------------------------

class TestForgePlannerRecallReference:
    def test_forge_planner_references_lore_recall_areas(self):
        """forge planner.md must reference `lore recall --areas` in its
        context-exploration / discovery step."""
        assert _FORGE_PLANNER.exists(), (
            f"forge planner.md not found at {_FORGE_PLANNER}. "
            "Check the path: tools/forge/plugins/forge/agents/planner.md"
        )
        text = _FORGE_PLANNER.read_text()
        assert "lore recall" in text, (
            "forge/agents/planner.md must reference `lore recall --areas` in its "
            "context-exploration step (D-9, Slice 3). Add a mention of running "
            "`lore recall --areas <names>` for the areas the task touches."
        )
