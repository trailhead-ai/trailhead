"""`/craft:drive`'s run-state checkpoint.

This task ships the driver's `## Driver run` checkpoint block — written onto the slice
parent at each phase boundary and read on resume, so the run is reconstructable from vault
records alone. It defines where the checkpoint is written and what resume does with it; the
phases that write it at their own boundaries are pinned by their own contract files.

Pinned here, using the wrap-aware `_pin` helper mirrored from `test_drive_skill_contract.py`
(itself mirrored from `test_execute_mode_contract.py`'s own helper, mirrored in turn from
ranger's `tests/test_sweep_contract.py`): every pinned span is asserted as a contiguous
substring **within one physical line**, so a markdown rewrap that shifts a line break fails
loudly as a wrap issue rather than reading as "phrase missing".
"""

from __future__ import annotations

from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
DRIVE_SKILL = CRAFT / "skills" / "drive" / "SKILL.md"


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


# --- contract item 1: the block is written at every phase boundary, not only the end ---


@pytest.mark.parametrize(
    "phase",
    ["select", "plan", "build", "pr-tail", "slice-close"],
)
def test_checkpoint_written_at_every_phase_boundary(phase):
    _pin(
        f"recording `**Phase:** {phase}`",
        f"the `{phase}` phase boundary must checkpoint the `## Driver run` block "
        "onto the slice parent, not only the final boundary.",
    )


def test_five_boundaries_named_as_a_closed_set():
    text = _text()
    for phase in ("select", "plan", "build", "pr-tail", "slice-close"):
        assert f"**{phase}**" in text, (
            f"drive/SKILL.md must name the `{phase}` boundary explicitly among "
            "the five phase boundaries it checkpoints"
        )


# --- contract item 2: resume re-enters at the phase after the one recorded -------------


def test_resume_reenters_at_the_phase_after_the_one_recorded():
    _pin(
        "re-enter at the phase after the one recorded",
        "A resume against a parent carrying a `## Driver run` block must "
        "re-enter one phase past whatever was last recorded, not restart or "
        "repeat the recorded phase.",
    )


def test_resume_reads_the_fixed_phase_order():
    _pin(
        "select → plan → build → pr-tail → slice-close",
        "The fixed phase order the resume logic walks must be stated "
        "explicitly so 'the phase after' is unambiguous.",
    )


def test_resume_reads_the_last_block_not_the_first():
    _pin(
        "Resume reads the **last** `## Driver run` block in the parent body",
        "Each boundary appends a fresh block, so resume must read the most "
        "recent one, never the first.",
    )


# --- contract item 3: no block resumes gated from the start ----------------------------


def test_no_block_resumes_gated_from_the_start():
    _pin(
        "Resume gated from the start of the run",
        "A slice parent carrying no `## Driver run` block must resume gated "
        "from the start rather than assuming any other mode.",
    )


def test_no_block_case_is_named_explicitly():
    _pin(
        "**No block present.**",
        "The no-block case must be named as its own explicit branch, not "
        "left implicit in the has-a-block branch's prose.",
    )


# --- contract item 4: a dirty branch on resume never silently re-dispatches build ------


def test_dirty_branch_never_silently_redispatches_build():
    _pin(
        "the driver never re-dispatches the build phase onto it on the assumption "
        "it is starting clean",
        "Finding commits already on the branch at resume must not silently "
        "re-dispatch the build phase.",
    )


def test_dirty_branch_escalates_under_a_named_trigger():
    _pin(
        "Escalate instead, under the `build-resume-dirty-branch` trigger",
        "The dirty-branch resume case must escalate under a named trigger "
        "rather than resolving itself or silently proceeding.",
    )


def test_dirty_branch_check_reads_the_craft_branch_label():
    _pin(
        "Read the parent's `craft/branch` label",
        "Branch-state resolution must read the existing `craft/branch` label "
        "convention rather than inventing a new signal.",
    )


def test_dirty_branch_mechanics_deferred_to_the_escalation_contract():
    _pin(
        "the escalation record's full mechanics are defined once in that "
        "contract, not restated here",
        "This site only names the trigger and the condition — the escalation "
        "record's contents belong to the escalation contract, matching the "
        "multi-repo escalation's own precedent, rather than being restated here.",
    )


# --- contract item 5: the write appends, preserving the value claim and plan sections --


def test_checkpoint_write_appends_not_replaces():
    _pin(
        "piping a unified diff that **appends** a fresh block",
        "The checkpoint write must append via a unified diff, never replace "
        "the parent body wholesale.",
    )


def test_checkpoint_write_names_the_destroy_risk_of_bare_stdin():
    _pin(
        "bare stdin to `lore record update` is a full-body replace and would "
        "destroy the record",
        "The destructive alternative must be named explicitly, matching "
        "`_shared/execute.md`'s own precedent for the same command.",
    )


def test_checkpoint_write_preserves_value_claim_and_plan_sections():
    _pin(
        "preserves the value claim and every plan section already on the parent",
        "The append must be stated to preserve the existing value-claim and "
        "plan sections rather than merely not mentioning them.",
    )


# --- contract item 6: the write runs through the credential-pattern scrub -------------


def test_checkpoint_write_runs_through_the_credential_scrub():
    _pin(
        "Run the block's text through the credential-pattern scrub",
        "The `## Driver run` block's write must run through the "
        "credential-pattern scrub before it lands in the vault.",
    )


def test_credential_scrub_references_shared_execute_phase_5():
    _pin(
        "(`_shared/execute.md`, [Phase 5](../_shared/execute.md#phase-5-flow-out))",
        "The scrub reference must point at `_shared/execute.md`'s Phase 5 "
        "definition rather than restating the regex list here.",
    )


# --- contract item 7: the resume check runs before the slice ritual, not after ---------


def test_resume_check_runs_before_the_slice_ritual_is_invoked():
    _pin(
        "This check runs before the slice ritual below is ever invoked, not after",
        "The resume check must be stated to run before the slice ritual, since "
        "`../slice/SKILL.md`'s own guard refuses on a non-terminal slice parent "
        "and every resume has exactly that.",
    )


def test_unconditional_slice_ritual_on_resume_is_named_as_the_failure_mode():
    _pin(
        "Running the slice ritual unconditionally on every entry, resume "
        "included, would hit that refusal and die before this checkpoint was "
        "ever read.",
        "The ritual must state explicitly why running the slice ritual "
        "unconditionally on a resume is a bug, not merely reorder the steps "
        "silently.",
    )


def test_slice_selection_only_runs_when_no_open_slice_to_resume():
    _pin(
        "Only run the slice selection below when there is no open slice parent "
        "to resume against.",
        "The slice ritual must run only when the resume check above found no "
        "open slice parent to resume.",
    )
