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


def _plan_phase_section() -> str:
    """The plan phase's own section body, bounded by the council-review heading."""
    text = DRIVE_SKILL.read_text()
    start = text.index("### 7. Run the plan phase")
    end = text.index("### 8.", start)
    return text[start:end]


# --- contract item 1: the plan phase runs plan's own procedure inline ---------------------


def test_plan_phase_heading_says_run_not_dispatch():
    _pin(
        "### 7. Run the plan phase",
        "The plan phase runs planning itself rather than dispatching it; naming the "
        "step 'Dispatch' is what smuggled the unattended planner in.",
    )


def test_plan_phase_reads_the_plan_skill_and_follows_it_inline():
    _pin(
        "Read `../plan/SKILL.md` now, in full, and follow it inline in this session",
        "The plan phase must defer to plan's own procedure by reading it, the same "
        "way the selection phase defers to ../slice/SKILL.md.",
    )


def test_plan_phase_takes_the_slice_rooted_path():
    _pin(
        "on its slice-rooted path",
        "The parent already exists, so planning must update it in place rather than "
        "creating a second parent.",
    )


def test_plan_phase_cites_the_slice_ritual_deferral_as_precedent():
    section = _plan_phase_section()
    assert "../slice/SKILL.md" in section, (
        "the plan phase must cite the selection phase's own read-it-don't-invoke-it "
        "deferral as the precedent it follows"
    )


def test_plan_phase_never_restates_planning():
    section = _plan_phase_section()
    assert "never restates" in section, (
        "a second copy of planning's procedure inside the driver is exactly how the "
        "two would drift apart, and the phase must say so"
    )


# --- contract item 2: the operator answers planning's own questions -----------------------


def test_clarify_step_asks_the_operator():
    _pin(
        "planning's own Clarify step asks the operator in this session",
        "A human is present, so planning's clarifying questions reach them rather "
        "than being suppressed and recorded as notes no one reads.",
    )


def test_approval_step_asks_the_operator():
    _pin(
        "planning's own Present for Approval step asks the operator in this session",
        "Plan approval is a genuine operator judgment; the driver must not answer it "
        "on the operator's behalf.",
    )


def test_plan_phase_passes_nothing_else_about_the_slice():
    _pin(
        "The parent record already carries the value claim",
        "The plan phase must read the slice's context from the parent record rather "
        "than restating it, matching this ritual's shape everywhere else.",
    )


# --- contract item 3: a plan that cannot be written escalates under a typed trigger -------


def test_plan_failure_escalates_under_plan_failed():
    _pin(
        "escalate under the `plan-failed` trigger",
        "A plan phase that cannot complete must write a typed escalation record like "
        "every other stop in this ritual.",
    )


def test_plan_failed_trigger_declared_in_vocabulary():
    _pin(
        "- **`plan-failed`** — the plan phase (step 7 above) cannot produce a plan",
        "Every trigger a phase raises must be declared in the closed vocabulary.",
    )


def test_plan_failure_carries_no_retry():
    section = _plan_phase_section()
    assert "no retry" in section, (
        "the plan phase's escalation must state no-retry explicitly, matching every "
        "other escalation site in this ritual"
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


def test_council_run_by_the_driver_against_the_plan_step_seven_wrote():
    _pin(
        "The driver runs that gate itself here, in this session, against the plan step 7 "
        "just wrote",
        "The council gate must be run by the driver itself against the plan the inline "
        "plan phase just wrote onto the slice parent.",
    )


def test_council_step_explains_why_the_gate_lives_at_the_drivers_altitude():
    _pin(
        "which is why the gate lives at the driver's altitude rather than inside the read above",
        "The ritual must say why the council is run here rather than left to planning's "
        "own step 8.5 — the checkpoint and the plan-critical escalation hang off it.",
    )


def test_council_step_cites_the_step_it_was_excluded_from():
    _pin(
        "stopped before that skill's own Council Review step (`../plan/SKILL.md`, step 8.5)",
        "The council step must name the step the inline plan read stopped before, or the "
        "exclusion at step 7 and the gate here can drift apart silently.",
    )
    plan_skill = CRAFT / "skills" / "plan" / "SKILL.md"
    assert "8.5" in plan_skill.read_text(), (
        "plan/SKILL.md must actually carry a step 8.5 for drive/SKILL.md's citation to "
        "be correct — cross-checked against the cited document itself, not asserted in "
        "isolation."
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


# --- contract item 7a: a plan-critical escalation checkpoints the plan phase too, so a --
# --- resume finds a phase to walk from instead of reconstructing a fresh invocation -----


def test_plan_critical_escalation_writes_the_plan_checkpoint_too():
    _pin(
        "write the `## Driver run` checkpoint block recording `**Phase:** plan` — the "
        "plan and its council review are both genuinely complete",
        "A plan-critical escalation must checkpoint the plan phase too, so a later "
        "resume finds a phase to walk from instead of finding no checkpoint at all "
        "and reconstructing the run as a fresh invocation.",
    )


def test_council_schema_states_critical_none_convention():
    _pin(
        "record an empty Critical list explicitly (`*Critical:* none`)",
        "The mirrored `## Council Review` schema must include plan/SKILL.md's own "
        "`*Critical:* none` convention, so a clean council is distinguishable from a "
        "section a skipped review would leave behind.",
    )


def test_council_schema_cites_plan_skill_critical_none_line():
    _pin(
        "matching `plan/SKILL.md`'s own convention (`plan/SKILL.md:348`)",
        "The `*Critical:* none` convention must cite the exact plan/SKILL.md line it "
        "mirrors.",
    )


# --- contract item 8: a clean council advances and checkpoints before the build phase -----


def test_clean_council_writes_the_plan_checkpoint():
    _pin(
        "Write the `## Driver run` checkpoint block recording `**Phase:** plan` (per step 4 "
        "above) once approval lands",
        "A clean council must write the plan-phase checkpoint block — and only once the "
        "operator's approval has landed, since an unapproved plan is not a completed phase.",
    )


def test_clean_council_checkpoint_precedes_the_build_dispatch():
    _pin(
        "before entering the build phase — step 9 below",
        "The plan-phase checkpoint must be written before the build phase is entered, and "
        "must point at the real build-phase step rather than a placeholder.",
    )


def test_inline_plan_read_stops_before_plannings_own_council_step():
    _pin(
        "stopping before that skill's own Council Review step (`../plan/SKILL.md`, step 8.5)",
        "The driver runs the council itself at step 8 — where the checkpoint and the "
        "plan-critical escalation hang off it — so an unscoped inline read of planning "
        "would run the four lenses twice.",
    )


def test_approval_runs_after_the_council_gate_not_before():
    _pin(
        "now run planning's own Present for Approval step (`../plan/SKILL.md`, step 9) inline",
        "Planning's approval step sits after its council gate in planning's own ordering; "
        "the driver must preserve that order rather than approving an unreviewed plan.",
    )


def test_declining_approval_ends_the_run():
    _pin(
        "An operator who declines to approve ends the run: escalate under the `plan-failed` trigger",
        "A declined plan is a stop like any other and must leave a typed record behind, "
        "not a silent halt.",
    )
