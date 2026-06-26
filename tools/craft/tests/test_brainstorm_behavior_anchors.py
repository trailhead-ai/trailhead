"""Behavior-preservation guard for brainstorm/SKILL.md trims.

The brainstorm skill has been compressed in two passes (a structural dedup,
then a deeper connective-prose trim). Compression must stay *behavior-preserving*:
the prose may shrink, but the behavior-bearing anchors — the discovery dimensions
the agent sweeps, the two mandatory decision steps, and the spec's canonical
section set — must survive. This guard pins those anchors so a future over-trim
fails loudly instead of silently dropping guidance.

These are content anchors, not wording locks: each asserts a short distinctive
phrase, not a paragraph, so legitimate rewording stays green.
"""

from __future__ import annotations

from pathlib import Path

_BRAINSTORM = (
    Path(__file__).parent.parent / "plugins" / "craft" / "skills" / "brainstorm" / "SKILL.md"
)


def _text() -> str:
    return _BRAINSTORM.read_text(encoding="utf-8")


# The eight "Poke at Edges" discovery dimensions — the heart of the skill. Each
# drives what the brainstorm surfaces; dropping one is a behavior change.
POKE_DIMENSIONS = [
    "Boundaries:",
    "Failure modes:",
    "Hidden assumptions:",
    "Scope:",
    "Reversibility:",
    "Migration / backfill:",
    "Failure visibility:",
    "Blast radius:",
]


def test_all_poke_dimensions_present():
    text = _text()
    missing = [d for d in POKE_DIMENSIONS if d not in text]
    assert not missing, (
        f"brainstorm/SKILL.md dropped Poke-at-Edges dimension(s) {missing} — these are "
        "behavior (what the agent surfaces), not trimmable connective prose."
    )


def test_spec_canonical_sections_present():
    # Compressed to a single inline list, but every canonical section name must remain
    # so a planner reading the skill (no CLI) still knows the spec shape.
    text = _text()
    for section in (
        "Problem",
        "Objectives",
        "Acceptance Criteria",
        "Non-Goals",
        "Constraints",
        "UI Direction",
        "Open Questions / Risks",
        "Related",
    ):
        assert section in text, (
            f"brainstorm/SKILL.md spec-section checklist lost {section!r} after compression"
        )


def test_recall_primary_and_spec_scaffold_anchors_present():
    text = _text()
    # `lore search` is the imperative primary lookup
    # (also covered by test_recall_wiring).
    assert "run `lore search" in text.lower()
    # spec scaffolding goes through the note_store seam + craft template
    # body, NOT `lore new spec`.
    assert "_shared/note-storage.md" in text
    assert "templates/spec.md" in text
    assert "lore new spec" not in text
