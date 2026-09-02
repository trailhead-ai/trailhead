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
PLANNER_AGENT = CRAFT / "agents" / "planner.md"


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


# --- contract item 2a: the planner dispatch carries a liveness deadline, mirroring the ---
# --- build phase's own, and a crashed (missing/empty outcome file) planner is mapped -----


def test_planner_dispatch_carries_a_liveness_deadline():
    _pin(
        "It bounds `craft:planner`'s own run the same way the build phase's own "
        "deadline bounds `craft:driver-worker`'s",
        "The planner dispatch must carry a liveness deadline rather than "
        "waiting on it indefinitely, matching the build phase's own dispatch.",
    )


def test_missing_or_empty_planner_outcome_file_escalates():
    _pin(
        "A missing or empty outcome file is read as a **crash**, not as "
        "still running, and escalates under the `planner-stalled` trigger",
        "A crashed planner — a missing or empty outcome file — must be "
        "mapped to an escalation, not left as an unmapped state with no "
        "typed record, the same hole the build phase's own worker-stalled "
        "mapping already closes.",
    )


def test_planner_stalled_trigger_declared_in_vocabulary():
    _pin(
        "**`planner-stalled`**",
        "The `planner-stalled` trigger raised by a crashed planner dispatch "
        "must be declared in the closed trigger vocabulary.",
    )


# --- contract item 2b: the dispatch overrides planner's clarify and wait-for-approval ----
# --- steps for the unattended dispatch — otherwise it hangs or invents answers -----------


def test_dispatch_overrides_planners_clarify_step():
    _pin(
        "override your own procedure's Clarify step",
        "The unattended planner dispatch must explicitly override planner's "
        "own Clarify step, since there is no human here to answer a question.",
    )


def test_dispatch_overrides_planners_approval_step():
    _pin(
        "Present for Approval step",
        "The unattended planner dispatch must explicitly override planner's "
        "own Present for Approval step, since the driver — never a task "
        "body — is what ships the plan onward.",
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


# --- contract item 4a: the context-pointer line carries a Spec: pointer, matching -----
# --- what plan/SKILL.md's own step 8.5 passes each lens ------------------------------


def test_context_pointer_names_a_spec_line():
    _pin(
        "Fill the context-pointer line with `Plan: <slice-parent-task-id>` and "
        "`Spec: <spec-path>`",
        "The council dispatch's context-pointer line must carry a `Spec:` "
        "pointer alongside the `Plan:` one, matching what plan/SKILL.md's own "
        "step 8.5 passes each lens — a lens told to read the spec with no "
        "pointer to it cannot.",
    )


# --- contract item 4b: <cross-cutting> carries plan-altitude's block, never the empty ---
# --- string, which is consult's substitution -------------------------------------------


def test_cross_cutting_is_not_the_empty_string():
    _pin(
        "never the empty string, which is `consult`'s substitution, not planning's",
        "The plan phase must state explicitly that substituting the empty "
        "string for <cross-cutting> is consult's substitution, not planning's "
        "— a driver-run council needs the plan-altitude block instead.",
    )


def test_cross_cutting_names_the_spec_drift_critical():
    _pin(
        "Spec drift: plan's tasks, summed, don't satisfy spec's acceptance criteria",
        "The plan-altitude cross-cutting block's spec-drift Critical — the "
        "single check a driver-run council most needs — must be supplied "
        "verbatim, matching plan/SKILL.md's own step 8.5 block.",
    )


def test_cross_cutting_names_the_hidden_scope_critical():
    _pin(
        "Hidden scope expansion: plan touches a subsystem the spec didn't claim",
        "The plan-altitude cross-cutting block's hidden-scope-expansion Critical "
        "must be supplied verbatim, matching plan/SKILL.md's own step 8.5 block.",
    )


def test_cross_cutting_names_the_reversibility_critical():
    _pin(
        "Reversibility unnamed: plan deploys something hard to roll back without "
        "naming rollback path",
        "The plan-altitude cross-cutting block's reversibility-unnamed Critical "
        "must be supplied verbatim, matching plan/SKILL.md's own step 8.5 block.",
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


def test_design_doc_step_reference_matches_planners_own_numbering():
    _pin(
        "whose own step 6.5 is a design-doc step",
        "The cited step number for planner's design-doc step must match "
        "planner.md's own numbering, not a stale reference.",
    )
    assert PLANNER_AGENT.read_text().count("### 6.5. Produce the Design Doc") == 1, (
        "planner.md must actually carry a step 6.5 design-doc heading for "
        "drive/SKILL.md's citation to be correct — cross-checking against the "
        "cited document itself, not just asserting the digit in isolation."
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


# --- contract item 6a: the driver persists its own council findings before escalating ----
# --- so the pointer above resolves to something written, not an empty section ------------


def test_driver_persists_council_review_section_before_escalating():
    _pin(
        "Append a `## Council Review` section to the slice parent",
        "The driver must write its own `## Council Review` section onto the "
        "plan record so the plan-critical escalation's pointer resolves to "
        "something, since nothing else in the driver's flow writes it.",
    )


def test_persisted_section_written_regardless_of_outcome():
    _pin(
        "Write it whether or not a Critical survives synthesis",
        "The persisted section must be written on every council run, not only "
        "when a Critical escalates, matching plan/SKILL.md's own persistence "
        "rule.",
    )


def test_persisted_schema_mirrors_plan_skill_step_8_5():
    _pin(
        "mirroring the schema `plan/SKILL.md` defines at its own step 8.5",
        "The persisted `## Council Review` section must mirror the schema "
        "plan/SKILL.md's own step 8.5 defines, so one section shape exists in "
        "the vault, not two.",
    )


def test_persisted_schema_cites_plan_skill_line_range():
    _pin(
        "(`plan/SKILL.md:328-347`)",
        "The schema reference must cite the exact line range plan/SKILL.md "
        "defines it at.",
    )


def test_persisted_section_is_one_line_per_finding_grouped_by_severity():
    _pin(
        "then `*Critical:*`, `*Important:*`, and `*Minor:*` lists, one line "
        "per finding, grouped by severity",
        "The persisted section must state the one-line-per-finding, "
        "grouped-by-severity shape explicitly.",
    )


def test_persisted_section_carries_no_disposition_text():
    _pin(
        "The driver writes the findings only: no disposition text for any "
        "Critical, since disposition is an operator judgment it does not make",
        "The persistence step must state explicitly that the driver writes no "
        "disposition text — the disposition boundary does not move.",
    )


def test_persisted_write_appends_via_diff_not_replace():
    _pin(
        "piping a unified diff the same way the `## Driver run` checkpoint "
        "does — bare stdin would replace the whole record body",
        "The persisted section must be appended via a unified diff, never a "
        "bare-stdin full-body replace.",
    )


def test_persisted_write_runs_through_the_credential_scrub():
    _pin(
        "run the section's text through the credential-pattern scrub before "
        "it is written",
        "The persisted `## Council Review` write must run through the "
        "credential-pattern scrub like every other record-body write in this "
        "ritual.",
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
