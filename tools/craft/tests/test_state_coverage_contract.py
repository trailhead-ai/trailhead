"""The state-coverage reference — a conformance test.

`skills/_shared/slice.md` is the sole carrier of the state-coverage reference: the
trigger for owing it (a slice that introduces or changes a visual surface), the floor
of states each slice archetype owes, the rule that a surface reachable by more than
one principal additionally owes an unauthorized state and ships its access check in
the same slice that opens the surface, the rule that a state arrives with the slice
introducing its surface and never earlier, and that the floor is explicitly
non-exhaustive.

It also carries the two written shapes three later documents depend on: the parent
task's `## Enumerated states` bullet-per-state shape, and the design doc's
`## State — <name>` heading-per-state shape, whose `<name>` must match the bullet's
text verbatim.

These tests pin that wording and pin that no other shipped file restates it.
"""

import re
from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SHARED_SLICE = CRAFT / "skills" / "_shared" / "slice.md"


def _text() -> str:
    return SHARED_SLICE.read_text()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _pin_normalized(phrase: str, reason: str) -> None:
    """Whitespace-insensitive pin: a rewrap that shifts a line break can't disarm it."""
    normalized = _normalize(_text())
    count = normalized.count(_normalize(phrase))
    assert count == 1, f"{reason} (found {count}): {phrase!r}"


def _archetype_section(name: str) -> str:
    normalized = _normalize(_text())
    idx = normalized.index(name)
    return normalized[idx : idx + 90]


def test_state_coverage_reference_ships_in_shared_slice():
    assert SHARED_SLICE.exists(), (
        f"Expected the state-coverage reference at {SHARED_SLICE}"
    )


def test_trigger_is_a_slice_that_introduces_or_changes_a_visual_surface():
    _pin_normalized(
        "A slice that introduces or changes a visual surface",
        "_shared/slice.md must state the state-coverage trigger",
    )


def test_read_only_collection_archetype_owes_its_four_states():
    section = _archetype_section("read-only collection")
    for state in ("zero", "one", "many", "collection-level failure"):
        assert state in section, (
            f"the read-only collection archetype must owe {state!r}"
        )


def test_single_record_view_archetype_owes_its_three_states():
    section = _archetype_section("single-record view")
    for state in ("found", "not-found", "record-level failure"):
        assert state in section, (
            f"the single-record view archetype must owe {state!r}"
        )


def test_mutation_archetype_owes_its_three_states():
    section = _archetype_section("mutation")
    for state in ("success", "validation failure", "concurrent-change"):
        assert state in section, f"the mutation archetype must owe {state!r}"


def test_long_running_action_archetype_owes_its_three_states():
    section = _archetype_section("long-running action")
    for state in ("in-flight", "completed", "failed"):
        assert state in section, (
            f"the long-running action archetype must owe {state!r}"
        )


def test_floor_is_stated_as_non_exhaustive():
    assert "non-exhaustive" in _text(), (
        "_shared/slice.md must state the state-coverage floor is non-exhaustive"
    )


def test_unauthorized_state_and_access_check_are_pinned_as_one_interaction():
    _pin_normalized(
        "Every archetype whose surface is reachable by more than one principal "
        "additionally owes an unauthorized state, and the slice that first makes "
        "that surface reachable ships its access check in that same slice rather "
        "than deferring it to a later one",
        "_shared/slice.md must state the unauthorized-state rule and the "
        "ship-the-access-check rule as one interaction, so deleting either half "
        "of the sentence fails this pin",
    )


def test_a_state_arrives_with_the_slice_introducing_its_surface_never_earlier():
    _pin_normalized(
        "A state arrives with the slice introducing the surface it belongs to, "
        "and never earlier",
        "_shared/slice.md must state that a state arrives with the slice "
        "introducing its surface, never earlier",
    )


def test_parent_enumerated_states_shape_is_one_bullet_per_state():
    _pin_normalized(
        "The parent task's `## Enumerated states` section is one `- <name>` "
        "bullet per state",
        "_shared/slice.md must pin the parent task's Enumerated states shape as "
        "one bullet per state",
    )


def test_design_doc_state_heading_shape_uses_the_bullet_name_verbatim():
    _pin_normalized(
        "The design doc carries one `## State — <name>` section per enumerated "
        "state, and each `<name>` is that bullet's text verbatim",
        "_shared/slice.md must pin the design doc's State heading shape and "
        "require the name match the parent bullet verbatim",
    )


def test_state_coverage_reference_has_a_single_carrier():
    phrase = "ships its access check in that same slice"
    carriers = sorted(
        p
        for p in CRAFT.rglob("*.md")
        if _normalize(phrase) in _normalize(p.read_text(encoding="utf-8"))
    )
    assert carriers == [SHARED_SLICE], (
        f"the state-coverage reference ({phrase!r}) must live in exactly one "
        f"shipped file (_shared/slice.md); found it in {carriers}"
    )
