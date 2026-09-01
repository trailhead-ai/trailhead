"""`craft:planner`'s write-the-plan step must not contradict plan/SKILL.md's two entry points.

`plan/SKILL.md` (pinned by `test_plan_slice_rooted_path.py`) discriminates a slice-rooted
dispatch (argument resolves to an existing slice-parent `task` record — update that parent,
write no spec status) from a topic-rooted one (create a new parent, advance a `ready` spec to
`planned`). `agents/planner.md`'s "Write the Plan" step used to do only the topic-rooted half
unconditionally — dispatched against a slice parent it would create a duplicate parent (exactly
what the slice-rooted path exists to prevent) and then advance the spec, tripping
`/craft:plan`'s `planned` refusal on the next pass. This file pins that the agent now names the
same discrimination, pointing at plan/SKILL.md rather than duplicating its prose.

Every pinned phrase is verified whole-file-unique before being asserted.
"""

from __future__ import annotations

from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
PLANNER = CRAFT / "agents" / "planner.md"


def _text() -> str:
    return PLANNER.read_text()


def _pin(phrase: str, reason: str) -> None:
    text = _text()
    assert text.count(phrase) == 1, (
        f"pinned phrase must be unique in the file (found {text.count(phrase)}): "
        f"{phrase!r}"
    )
    assert any(phrase in line for line in text.splitlines()), reason


def test_write_the_plan_names_the_slice_rooted_case():
    _pin(
        "dispatched against an existing slice-parent `task` record",
        "agents/planner.md's Write the Plan step must name the slice-rooted "
        "case explicitly: dispatched against an existing slice-parent task "
        "record, not just a topic.",
    )


def test_write_the_plan_points_at_plan_skill_for_the_rule_not_a_duplicate():
    _pin(
        "`skills/plan/SKILL.md`'s Entry Point section",
        "agents/planner.md must point at plan/SKILL.md's Entry Point section "
        "for the two-path discrimination rather than restating it — one file "
        "owns the rule.",
    )


def test_write_the_plan_does_not_unconditionally_create_a_second_parent():
    text = _text()
    assert (
        "`printf '%s' \"$BODY\" | lore record create "
        "--kind task" in text
    ), (
        "the topic-rooted create-parent command must still exist for the "
        "topic-rooted case"
    )
    assert "only on the topic-rooted path" in text, (
        "agents/planner.md must scope parent creation to the topic-rooted "
        "path explicitly, matching plan/SKILL.md's slice-rooted path, which "
        "updates the existing parent instead of creating a second one."
    )


def test_write_the_plan_advances_no_spec_on_either_path():
    _pin(
        "Neither path advances the spec's status",
        "agents/planner.md must state that neither the slice-rooted nor the "
        "topic-rooted path advances a spec's status, matching plan/SKILL.md.",
    )
