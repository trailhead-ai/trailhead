"""The canonical slice/task vocabulary and quality bar — a conformance test.

Craft is gaining a per-slice delivery loop
([[spec/specs-are-delivered-one-vertical-slice-at-a-time]]), and its two units of
work — the vertical increment and the component-shaped unit beneath it — need one
place that defines them, so every skill's ritual text can point at the same wording
instead of drifting into independent paraphrases. `_shared/slice.md` is that place.

This task only ships the definition file and this test. Nothing else in craft reads
the file yet — later tasks in this slice point the existing prose at it.
"""

from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SHARED_SLICE = CRAFT / "skills" / "_shared" / "slice.md"


def test_shared_slice_definition_ships():
    assert SHARED_SLICE.exists(), (
        f"Expected the single-source slice/task definition at {SHARED_SLICE}"
    )


def test_slice_is_defined_as_a_vertical_increment():
    text = SHARED_SLICE.read_text()
    assert (
        "vertical increment" in text
    ), "_shared/slice.md must define 'slice' as a vertical increment"
    assert "observably more valuable to its consumer" in text, (
        "_shared/slice.md must state that a slice's completion leaves the system "
        "observably more valuable to its consumer"
    )


def test_task_is_defined_as_the_component_shaped_unit_beneath_a_slice():
    assert "component-shaped unit beneath a slice" in SHARED_SLICE.read_text(), (
        "_shared/slice.md must define 'task' as the component-shaped unit beneath a "
        "slice"
    )


def test_quality_bar_names_valuable_small_and_testable():
    text = SHARED_SLICE.read_text()
    for word in ("Valuable", "Small", "Testable"):
        assert word in text, f"_shared/slice.md must name {word!r} as part of the quality bar"


def test_quality_bar_rejects_independent_with_a_stated_reason():
    text = SHARED_SLICE.read_text()
    assert "Independent" in text, (
        "_shared/slice.md must mention 'Independent' explicitly to reject it"
    )
    assert "slices build on each other by design" in text, (
        "_shared/slice.md must state the reason Independent is rejected — a bare "
        "mention of the word does not pin the reason"
    )


def test_value_floor_reads_against_the_spec_own_consumer():
    text = SHARED_SLICE.read_text()
    assert "the spec's own consumer" in text, (
        "_shared/slice.md must read the value floor against the spec's own consumer, "
        "not an end user"
    )
    assert "all callers migrated" in text, (
        "_shared/slice.md must carry the all-callers-migrated contrast that "
        "distinguishes a cleared floor from an uncleared one"
    )


def test_selection_rule_is_smallest_next_and_denies_a_global_ranking():
    text = SHARED_SLICE.read_text()
    assert "smallest-next" in text, (
        "_shared/slice.md must state the selection rule as smallest-next above the "
        "value floor"
    )
    assert "not the rule" in text, (
        "_shared/slice.md must explicitly deny that a pre-committed global value "
        "ranking is the rule"
    )


def test_enabler_carve_out_names_all_three_constraints():
    text = SHARED_SLICE.read_text()
    assert "naming what it enables" in text, (
        "_shared/slice.md must require the enabler justification to name what it "
        "enables"
    )
    assert "cannot be folded into the slice needing it" in text, (
        "_shared/slice.md must require the justification to state why the enabler "
        "cannot be folded into the slice that needs it"
    )
    assert "the slice consuming it comes next" in text, (
        "_shared/slice.md must require that the slice consuming the enabler comes "
        "next"
    )


def test_enabler_justification_writes_no_record():
    assert "writes no record" in SHARED_SLICE.read_text(), (
        "_shared/slice.md must state that naming the consuming slice in the "
        "justification writes no record for that slice"
    )
