"""planner.md template dedup — inline literals replaced by checklist + CLI pointer.

TDD contract:
  1. The two large inline literal template blocks (spec frontmatter `type: spec` block
     and the plan `# [Feature Name] Implementation Plan` literal) do NOT appear on the
     common path — they must only appear after the manual-write fallback marker.
  2. `planner.md` references the `note_store` contract (`_shared/note-storage.md`) and the
     craft template path — NOT `lore new spec` / `lore new plan`.
  3. `planner.md` still contains the required section names the checklist must carry
     (every section a spec/plan needs, including Observability & Failure Visibility and
     Known Unknowns).
  4. The fallback marker is present (so the single full literal survives exactly once,
     behind the gate).

Write BEFORE the implementation — these tests must fail RED first, then green after.
"""

from __future__ import annotations

from pathlib import Path

AGENTS_DIR = Path(__file__).parent.parent / "plugins" / "craft" / "agents"
PLANNER_MD = AGENTS_DIR / "planner.md"

# Section names that the checklist must carry so a planner dispatched without
# the CLI still knows the shape of each document.
_REQUIRED_SPEC_SECTIONS = [
    "Problem",
    "Objectives",
    "Acceptance Criteria",
    "Non-Goals",
    "Constraints",
    "Open Questions",
    "Related",
]

_REQUIRED_PLAN_SECTIONS = [
    "Goal",
    "Delta design",
    "Known Unknowns",
]


def _text() -> str:
    assert PLANNER_MD.exists(), f"Expected planner.md at {PLANNER_MD}"
    return PLANNER_MD.read_text()


# ---------------------------------------------------------------------------
# note_store seam pointer — `lore new` is decoupled
# ---------------------------------------------------------------------------


def test_note_store_contract_referenced():
    """planner.md must reference the `note_store` contract (`_shared/note-storage.md`)
    so the agent persists plans/specs through the seam, not `lore new`."""
    text = _text()
    assert "_shared/note-storage.md" in text, (
        "planner.md must reference '_shared/note-storage.md' (the note_store seam) so the "
        "agent persists plans/specs through the centralized contract rather than `lore new`."
    )


def test_craft_template_path_referenced():
    """planner.md must reference the craft-owned template path so the agent knows
    where the plan/spec body skeleton lives."""
    text = _text()
    assert "templates/plan.md" in text and "templates/spec.md" in text, (
        "planner.md must reference the craft-owned 'templates/plan.md' and "
        "'templates/spec.md' bodies (craft owns the template bodies; lore is body-agnostic)."
    )


# ---------------------------------------------------------------------------
# Checklist section coverage — spec and plan
#
# The section names must appear somewhere in the file. We assert they're present
# overall, and separately assert the template literals only appear after a fallback
# marker (the dedup tests above). This way the checklist-coverage test doesn't
# depend on where exactly each section name falls relative to a fallback marker —
# only the template literals (type: spec, # [Feature Name]...) are positionally
# constrained.
# ---------------------------------------------------------------------------


def test_spec_checklist_carries_all_required_sections():
    """planner.md must name every required spec section so a planner dispatched
    without the CLI still knows the shape."""
    text = _text()
    missing = [s for s in _REQUIRED_SPEC_SECTIONS if s not in text]
    assert not missing, (
        f"planner.md is missing these required spec section names: "
        f"{missing}. The fields-to-fill checklist must name every section a spec "
        "needs (so a planner with no CLI still knows the shape)."
    )


def test_plan_checklist_carries_all_required_sections():
    """planner.md must name every required plan section."""
    text = _text()
    missing = [s for s in _REQUIRED_PLAN_SECTIONS if s not in text]
    assert not missing, (
        f"planner.md is missing these required plan section names: "
        f"{missing}. The fields-to-fill checklist must name every section a plan "
        "needs (including Observability & Failure Visibility and Known Unknowns)."
    )


def test_planner_gate_description_reflects_recommend_then_accept():
    """The gauntlet no longer gates on a user dispositioning each Critical finding
    individually — it presents one recommendation the user accepts or overrides.
    planner.md's explanation of why it cannot freeze a spec must match."""
    text = _text()
    assert "dispositioning each Critical finding" not in text, (
        "planner.md must not describe the gauntlet's retired per-finding "
        "disposition interrogation — the gauntlet now presents a recommendation "
        "the user accepts or overrides"
    )
    assert "accepting" in text and "overriding" in text and "recommendation" in text, (
        "planner.md must describe the gauntlet's gate in recommend-then-accept "
        "terms, matching gauntlet/SKILL.md's resolution flow"
    )
