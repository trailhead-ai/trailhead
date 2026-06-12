"""Slice 4: planner.md template dedup — inline literals replaced by checklist + CLI pointer.

TDD contract (Slice 4):
  1. The two large inline literal template blocks (spec frontmatter `type: spec` block
     and the plan `# [Feature Name] Implementation Plan` literal) do NOT appear on the
     common path — they must only appear after the manual-write fallback marker.
  2. `planner.md` still references `lore new spec` and `lore new plan` (CLI pointer preserved).
  3. `planner.md` still contains the required section names the checklist must carry
     (every section a spec/plan needs, including Observability & Failure Visibility and
     Known Unknowns).
  4. The fallback marker is present (so the single full literal survives exactly once,
     behind the gate).

Write BEFORE the implementation — these tests must fail RED first, then green after.
"""

from __future__ import annotations

from pathlib import Path

AGENTS_DIR = Path(__file__).parent.parent / "plugins" / "forge" / "agents"
PLANNER_MD = AGENTS_DIR / "planner.md"

# The greppable fallback-gate marker introduced in this slice. Any text that
# only appears AFTER this marker is on the manual-write fallback path.
FALLBACK_MARKER = "lore CLI is unavailable"

# Literals that belong to the full inline template blocks and must NOT appear
# on the common path — they may only appear after FALLBACK_MARKER.
_SPEC_TEMPLATE_LITERAL = "type: spec"
_PLAN_TEMPLATE_LITERAL = "# [Feature Name] Implementation Plan"

# Section names that the checklist must carry so a planner dispatched without
# the CLI still knows the shape of each document.
_REQUIRED_SPEC_SECTIONS = [
    "Problem",
    "Objectives",
    "Acceptance Criteria",
    "Non-Goals",
    "Constraints",
    "Observability & Failure Visibility",
    "Open Questions",
    "Related",
]

_REQUIRED_PLAN_SECTIONS = [
    "Goal",
    "Architecture",
    "Observability & Failure Visibility",
    "Known Unknowns",
    "Slices",
]


def _text() -> str:
    assert PLANNER_MD.exists(), f"Expected planner.md at {PLANNER_MD}"
    return PLANNER_MD.read_text()


def _fallback_offset(text: str) -> int:
    """Return the character offset where the fallback section starts, or -1."""
    idx = text.find(FALLBACK_MARKER)
    return idx


# ---------------------------------------------------------------------------
# Core dedup assertions
# ---------------------------------------------------------------------------


def test_spec_template_literal_absent_from_common_path():
    """The spec frontmatter literal `type: spec` must not appear on the common
    path — only after the manual-write fallback marker (if at all).

    Fail first: currently the full template is inline before any fallback gate.
    """
    text = _text()
    fallback_idx = _fallback_offset(text)
    spec_idx = text.find(_SPEC_TEMPLATE_LITERAL)

    assert spec_idx == -1 or (
        fallback_idx != -1 and spec_idx > fallback_idx
    ), (
        f"planner.md contains {_SPEC_TEMPLATE_LITERAL!r} at char {spec_idx} "
        f"but the fallback marker '{FALLBACK_MARKER}' is at char {fallback_idx}. "
        "The spec template literal must only appear after the fallback marker "
        "(or not at all). Replace the common-path template block with a "
        "fields-to-fill checklist."
    )


def test_plan_template_literal_absent_from_common_path():
    """The plan header literal `# [Feature Name] Implementation Plan` must not
    appear on the common path — only after the manual-write fallback marker.

    Fail first: currently the full template is inline before any fallback gate.
    """
    text = _text()
    fallback_idx = _fallback_offset(text)
    plan_idx = text.find(_PLAN_TEMPLATE_LITERAL)

    assert plan_idx == -1 or (
        fallback_idx != -1 and plan_idx > fallback_idx
    ), (
        f"planner.md contains {_PLAN_TEMPLATE_LITERAL!r} at char {plan_idx} "
        f"but the fallback marker '{FALLBACK_MARKER}' is at char {fallback_idx}. "
        "The plan template literal must only appear after the fallback marker "
        "(or not at all). Replace the common-path template block with a "
        "fields-to-fill checklist."
    )


# ---------------------------------------------------------------------------
# CLI pointer preserved
# ---------------------------------------------------------------------------


def test_lore_new_spec_referenced():
    """planner.md must still reference `lore new spec` so the agent scaffolds
    specs via the CLI on the common path."""
    text = _text()
    assert "lore new spec" in text, (
        "planner.md must reference 'lore new spec' (CLI pointer for spec scaffolding). "
        "The CLI call is the common path; the fallback is manual-write only."
    )


def test_lore_new_plan_referenced():
    """planner.md must still reference `lore new plan` so the agent scaffolds
    plans via the CLI on the common path."""
    text = _text()
    assert "lore new plan" in text, (
        "planner.md must reference 'lore new plan' (CLI pointer for plan scaffolding). "
        "The CLI call is the common path; the fallback is manual-write only."
    )


# ---------------------------------------------------------------------------
# Fallback marker present (full literal survives exactly once, behind the gate)
# ---------------------------------------------------------------------------


def test_fallback_marker_present():
    """The manual-write fallback marker must be present so there is a clear
    gate — the full literal template survives exactly once, behind it."""
    text = _text()
    assert FALLBACK_MARKER in text, (
        f"planner.md must contain the fallback marker {FALLBACK_MARKER!r} to gate "
        "the manual-write path. Add a 'If the lore CLI is unavailable, write the "
        "file by hand mirroring this shape:' gate before the retained literal."
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
