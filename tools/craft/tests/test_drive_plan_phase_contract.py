"""`/craft:drive`'s plan phase.

This task ships the driver's plan phase: dispatching `craft:planner` against the chosen slice
parent with a pinned outcome-file grammar (planner declares no outcome-file mechanism of its
own), reading the plan result from that file rather than the agent's reply, running the
mandatory council review itself (the four lenses, dispatched and adjudicated in this session
per `_shared/council.md`'s dispatch contract, since planner's own tool grant carries no `Agent`
tool), escalating a surviving Critical under the `plan-critical` trigger with a pointer rather
than any disposition, and advancing a clean council by writing the phase checkpoint before the
build phase, which `test_drive_build_phase_contract.py` pins.

Pinned here, using the wrap-aware `_pin` helper mirrored from `test_drive_escalation_contract.py`
(itself mirrored from `test_drive_resume_contract.py`, `test_drive_skill_contract.py`,
`test_execute_mode_contract.py`, and ranger's `tests/test_sweep_contract.py`): every pinned span
is asserted as a contiguous substring **within one physical line**, so a markdown rewrap that
shifts a line break fails loudly as a wrap issue rather than reading as "phrase missing".
"""

from __future__ import annotations

from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
DRIVE_SKILL = CRAFT / "skills" / "drive" / "SKILL.md"
COUNCIL_SHARED = CRAFT / "skills" / "_shared" / "council.md"


def _text() -> str:
    return DRIVE_SKILL.read_text()


def _pin(phrase: str, why: str, path: Path = DRIVE_SKILL) -> None:
    """Assert *phrase* appears inside a single physical line of *path*."""
    text = path.read_text()
    if any(phrase in line for line in text.splitlines()):
        return
    if phrase in " ".join(text.split()):
        pytest.fail(
            f"{path.name}: the pinned span {phrase!r} is present but straddles a line "
            f"wrap — keep it on one physical line. {why}"
        )
    pytest.fail(f"{path.name}: missing the pinned span {phrase!r}. {why}")


# --- contract item 1: the planner dispatch names the slice parent and an outcome file, ---
# --- and passes nothing else about the slice ----------------------------------------------


def test_planner_dispatch_names_the_chosen_slice_parent():
    _pin(
        "dispatch `craft:planner` against that slice parent on the slice-rooted path",
        "The plan phase must dispatch `craft:planner` against the chosen slice parent, "
        "on its slice-rooted path.",
    )


def test_planner_dispatch_passes_nothing_else_about_the_slice():
    _pin(
        "Pass it nothing else about the slice",
        "The dispatch must pass nothing else about the slice beyond the parent record "
        "itself and the outcome-file instruction.",
    )


def test_planner_dispatch_names_an_outcome_file():
    _pin(
        "Outcome file: <outcome-file-path>",
        "The dispatch prompt must name an outcome file for planner to write its result to.",
    )


# --- contract item 2: the dispatch pins its own outcome grammar in full -------------------


def test_states_planner_has_no_outcome_file_mechanism_of_its_own():
    _pin(
        "`craft:planner` declares no outcome-file mechanism of its own, so there is no "
        "default grammar to override",
        "The ritual must state explicitly why the whole grammar is pinned in the dispatch "
        "prompt rather than relying on a default planner already has.",
    )


def test_pins_the_grammar_the_way_ranger_execute_does():
    _pin(
        "the way `ranger:execute` pins its own outcome grammar to the agent it dispatches",
        "The pinned-grammar approach must be modelled explicitly on ranger's execute agent, "
        "the precedent the intent document names.",
    )


@pytest.mark.parametrize(
    "token",
    [
        "`PLANNED <slice-parent-task-id>`",
        "`BLOCKED <reason>`",
        "`NEEDS_CONTEXT <reason>`",
    ],
)
def test_outcome_grammar_declares_each_token(token):
    _pin(
        token,
        f"the pinned outcome grammar must declare the {token} token for planner to write.",
    )


def test_outcome_grammar_supersedes_any_default():
    _pin(
        "This outcome grammar supersedes any default your own procedure names",
        "The dispatch prompt must state that this grammar supersedes any default "
        "planner's own procedure names, matching ranger's execute agent precedent.",
    )


def test_outcome_file_carries_nothing_but_the_one_line():
    _pin(
        "Do not write a summary, a file list, or anything else to the outcome file",
        "The dispatch prompt must forbid anything beyond the single outcome line.",
    )


# --- contract item 3: the ritual reads the plan result from the outcome file, never reply --


def test_reads_plan_result_from_outcome_file_never_the_reply():
    _pin(
        "Read the plan result from the outcome file above, never from the agent's reply",
        "The driver must read the plan phase's result from the outcome file, never from "
        "the dispatched agent's reply, matching the build phase's own worker channel rule.",
    )


def test_planned_line_advances_into_council_review():
    _pin(
        "A `PLANNED <slice-parent-task-id>` line advances into the council review below",
        "A `PLANNED` outcome line must advance the ritual into the council review step.",
    )


# --- contract item 4: the driver dispatches all four council lenses itself ----------------


@pytest.mark.parametrize("lens", ["builder", "breaker", "attacker", "advocate"])
def test_dispatches_each_council_lens(lens):
    _pin(
        f"`{lens}`",
        f"the plan phase must dispatch the `{lens}` council lens itself.",
    )


def test_council_dispatch_defers_to_shared_council_contract():
    assert COUNCIL_SHARED.exists(), (
        f"drive/SKILL.md defers to {COUNCIL_SHARED}, which must exist for the seam to be real"
    )
    _pin(
        "per `_shared/council.md`'s dispatch contract",
        "The council dispatch must name and defer to the shared council contract rather "
        "than inventing its own.",
    )


def test_council_dispatch_never_restates_the_shared_contract():
    _pin(
        "do not restate its roster, prompt template, or bars here",
        "The ritual must state explicitly that it never restates council.md's roster, "
        "prompt template, or bars — a second copy is how the two would drift apart.",
    )


def test_driver_is_the_synthesizer_in_session():
    _pin(
        "The driver is the synthesizer, in session, never a subagent",
        "The driver must adjudicate the four lenses' returns itself, in session, never "
        "delegating synthesis to a subagent.",
    )


def test_council_dispatched_against_a_plan_planner_wrote():
    _pin(
        "The driver runs the council itself, in this session, against the plan now written "
        "on the slice parent",
        "The council must be run by the driver itself against the plan craft:planner just "
        "wrote, since the planner agent structurally cannot run it.",
    )


def test_states_planner_has_no_agent_tool():
    _pin(
        "whose tool grant carries no `Agent` tool at all",
        "The ritual must state why planner cannot run the council itself — its tool grant "
        "carries no Agent tool.",
    )


# --- contract item 5: a council Critical escalates under plan-critical, no disposition ----


def test_council_critical_escalates_under_plan_critical_trigger():
    _pin(
        "Any Critical surviving synthesis is an escalation under the `plan-critical` trigger",
        "A council Critical surviving synthesis must escalate under the `plan-critical` "
        "trigger, following the shared escalation contract.",
    )


def test_disposition_is_named_as_an_operator_judgment():
    _pin(
        "Disposition is an operator judgment, the same as the gauntlet's operator-only "
        "dispositions",
        "The ritual must name disposition as an operator judgment the driver never makes, "
        "matching the gauntlet's operator-only disposition precedent.",
    )


# --- contract item 6: the escalation names a pointer to the plan record's council section --


def test_escalation_names_pointer_to_plan_records_council_section():
    _pin(
        "the plan record and its `## Council Review` section",
        "The plan-critical escalation must name a pointer to the plan record's own "
        "`## Council Review` section rather than restating the finding.",
    )


# --- contract item 7: the driver authors no disposition reason in any path ---------------
# --- (absence-shaped: pinned as the positive statement, not a forbidden-word check) -------


def test_escalation_carries_pointer_never_a_drafted_verdict():
    _pin(
        "never a drafted verdict or a recommended resolution",
        "The escalation must be stated to carry a pointer only — never a drafted verdict "
        "or a recommended resolution the driver authored.",
    )


def test_driver_authors_none_of_its_own_disposition():
    _pin(
        "so the driver authors none of its own",
        "The ritual must state plainly that the driver authors no disposition of its own "
        "for a council Critical.",
    )


# --- contract item 8: a clean council advances and checkpoints before the build phase -----


def test_clean_council_writes_the_plan_checkpoint():
    _pin(
        "No Critical survives synthesis: write the `## Driver run` checkpoint block "
        "recording `**Phase:** plan`",
        "A clean council (no surviving Critical) must write the plan-phase checkpoint "
        "block.",
    )


def test_clean_council_checkpoint_precedes_the_build_dispatch():
    _pin(
        "before dispatching the build phase — step 9 below",
        "The plan-phase checkpoint must be written before the build phase is dispatched, "
        "and the dispatch must point at the real build-phase step rather than the old "
        "'a later task' placeholder.",
    )
