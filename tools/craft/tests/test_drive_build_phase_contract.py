"""`/craft:drive`'s build phase.

The driver runs craft's shared execute procedure **inline, in its own session**, against
the slice parent's child task graph — reading `_shared/execute.md` and following it, the
same read-it-don't-invoke-it deferral the selection phase already applies to
`../slice/SKILL.md`. Running inline selects the shared procedure's **attended** mode, which
is the point: a human is present, so every escalation the procedure names asks them rather
than parking a question no one will read.

Pinned here, using the wrap-aware `_pin` helper mirrored from
`test_drive_plan_phase_contract.py` (itself mirrored from `test_drive_escalation_contract.py`,
`test_drive_resume_contract.py`, `test_drive_skill_contract.py`, `test_execute_mode_contract.py`,
and ranger's `tests/test_sweep_contract.py`): every pinned span is asserted as a contiguous
substring **within one physical line**, so a markdown rewrap that shifts a line break fails
loudly as a wrap issue rather than reading as "phrase missing".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
DRIVE_SKILL = CRAFT / "skills" / "drive" / "SKILL.md"
SHARED_EXECUTE = CRAFT / "skills" / "_shared" / "execute.md"


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


def _build_phase_section() -> str:
    """The build phase's own section body, bounded by the next `### ` heading."""
    text = DRIVE_SKILL.read_text()
    start = text.index("### 9. Run the build phase")
    end = text.index("### 10.", start)
    return text[start:end]


# --- the build phase runs the shared procedure inline, never through a dispatch ---------


def test_build_phase_heading_says_run_not_dispatch():
    _pin(
        "### 9. Run the build phase",
        "The build phase runs the shared execute procedure itself; naming the step "
        "'Dispatch' is what smuggled an unattended worker in the first place.",
    )


def test_build_phase_reads_the_shared_procedure_and_follows_it_inline():
    _pin(
        "Read `../_shared/execute.md` now, in full, and follow it inline in this session",
        "The build phase must defer to the shared execute procedure by reading it, the "
        "same way the selection phase defers to ../slice/SKILL.md.",
    )


def test_build_phase_cites_the_slice_ritual_deferral_as_precedent():
    section = _build_phase_section()
    assert "../slice/SKILL.md" in section, (
        "the build phase must cite the selection phase's own read-it-don't-invoke-it "
        "deferral as the precedent it follows — without the citation the inline read "
        "reads as an ad-hoc choice rather than this ritual's established shape"
    )


def test_build_phase_never_restates_the_shared_procedure():
    _pin(
        "This skill never restates that procedure",
        "A second copy of the execute procedure inside the driver is exactly how the "
        "two would drift apart.",
    )


# --- running inline is what selects attended mode, and that is deliberate ---------------


def test_inline_run_selects_attended_mode():
    _pin(
        "Running it inline in this session selects the shared procedure's **attended** mode",
        "The driver must state that an inline run is attended — that is the mode "
        "selection, and it is the reason the phase runs inline at all.",
    )


def test_attended_mode_is_named_as_deliberate_not_incidental():
    _pin(
        "a human is present in this session, so every escalation point asks them",
        "Attended mode must be justified by the human actually being present, not "
        "left as an accident of how the procedure was invoked.",
    )


def test_build_phase_states_it_never_invokes_craft_execute():
    _pin(
        "never invokes `/craft:execute`",
        "The build phase must state explicitly that it never invokes `/craft:execute` "
        "— skill-to-skill chaining is unreliable by `/craft:plan`'s own rule, which is "
        "why the procedure is read rather than the skill invoked.",
    )


# --- the checkpoint still brackets the phase -------------------------------------------


def test_checkpoint_written_before_and_after_the_build_phase():
    _pin(
        "The checkpoint at this boundary is written before and after the build phase",
        "A crash inside the build phase must stay distinguishable from a crash before "
        "it, which is what the bracketing checkpoints record.",
    )


def test_before_checkpoint_is_the_plan_phase_block():
    _pin(
        "recording `**Phase:** plan` already written at step 8 is the before-checkpoint",
        "The plan-phase checkpoint is what marks the build phase as entered.",
    )


def test_after_checkpoint_records_phase_build():
    _pin(
        "writing the block recording `**Phase:** build` here is the after-checkpoint",
        "Completing the build phase must record `**Phase:** build` so a resume knows "
        "the PR tail is next.",
    )


def test_resume_table_build_entry_points_at_step_nine():
    text = DRIVE_SKILL.read_text()
    assert re.search(r"once the build phase \(step 9 below\) completes", text), (
        "the checkpoint table's build row must point at the step that actually runs "
        "the build, or a resume walks to a step number that no longer exists"
    )


# --- a build that cannot complete escalates under a typed trigger ----------------------


def test_build_failure_escalates_under_build_failed():
    _pin(
        "escalate under the `build-failed` trigger",
        "A build phase that cannot complete must write a typed escalation record like "
        "every other stop in this ritual, rather than halting on an in-session report.",
    )


def test_build_failed_trigger_declared_in_vocabulary():
    _pin(
        "- **`build-failed`** — the build phase (step 9 above) cannot complete the slice",
        "Every trigger a phase raises must be declared in the closed vocabulary; an "
        "undeclared trigger is free text by another name.",
    )


def test_build_failure_carries_no_retry():
    section = _build_phase_section()
    assert "no retry" in section, (
        "the build phase's escalation must state no-retry explicitly, matching every "
        "other escalation site in this ritual"
    )
