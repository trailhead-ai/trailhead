"""`/craft:drive`'s slice close and the boundary stop.

This task ships the driver's slice close: on a closing PR-tail outcome, the driver marks the
slice parent `done`, writes the final `## Driver run` checkpoint recording `slice-close`, and
stops — reporting the value claim, what shipped, and a fully formed re-entry command. The ledger
append that records the closed slice is `../slice/SKILL.md`'s own work on its next pass, never
the driver's: the driver's close writes the parent's status and the checkpoint, nothing else.
Re-entry is the operator's act — the driver never crosses the boundary or selects the next slice
on its own initiative.

Pinned here, using the wrap-aware `_pin` helper mirrored from `test_drive_portage_tail_contract.py`
(itself mirrored from `test_drive_build_phase_contract.py`, `test_drive_plan_phase_contract.py`,
`test_drive_escalation_contract.py`, `test_drive_resume_contract.py`,
`test_drive_skill_contract.py`, `test_execute_mode_contract.py`, and ranger's
`tests/test_sweep_contract.py`): every pinned span is asserted as a contiguous substring
**within one physical line**, so a markdown rewrap that shifts a line break fails loudly as a
wrap issue rather than reading as "phrase missing".
"""

from __future__ import annotations

import re
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


def _close_section() -> str:
    """The slice-close step's own text, isolated from the rest of the ritual."""
    text = _text()
    match = re.search(r"### 11\..*?(?=\n## )", text, re.S)
    assert match, "drive/SKILL.md must carry a step 11 (slice close) section"
    return match.group(0)


# --- contract item 1: a closing outcome marks the slice parent done and the driver halts ----


def test_closing_outcome_marks_the_slice_parent_done():
    _pin(
        "Mark the slice parent `done`",
        "A closing PR-tail outcome must mark the slice parent `done`.",
    )


def test_done_write_uses_the_slice_parent_status_flag():
    _pin(
        "lore record update task/<slice-parent-name> --status done --vault <elected-vault>",
        "The done write must be a concrete, runnable command against the slice parent.",
    )


def test_driver_halts_rather_than_selecting_again():
    _pin(
        "the driver does not invoke `/craft:slice` or `/craft:drive` again on its own "
        "initiative, and does not cross this boundary itself",
        "The driver must halt at the boundary rather than selecting or re-entering "
        "on its own initiative.",
    )


# --- contract item 2: the report names a fully formed re-entry command, no placeholder ------


def test_close_report_names_a_concrete_reentry_command():
    _pin(
        "e.g. `/craft:drive spec/streaming-export`, with this run's own resolved spec "
        "name substituted in",
        "The slice-close report must show a concrete, runnable re-entry command with a "
        "real spec name substituted in, matching the escalation report's own precedent, "
        "rather than left as bare template text.",
    )


def test_close_report_forbids_the_literal_placeholder():
    _pin(
        "never the literal `<spec-name>` template text",
        "The slice-close report must explicitly forbid emitting the literal placeholder "
        "text — the run's own resolved spec name must be substituted in.",
    )


def test_close_report_names_the_value_claim_and_what_shipped():
    _pin(
        "the slice's value claim, already stated when the slice was chosen at step 6",
        "The close report must name the value claim.",
    )
    _pin(
        "what shipped — the branch or PR reference named by whichever closing token "
        "step 10 mapped",
        "The close report must name what shipped.",
    )


# --- contract item 3: the driver writes no `## Slices` ledger line itself -------------------
# (pinned positively — the ownership statement, not an absence-shaped "forbidden string
# is missing" check — plus a mutation-provable scan of the close section's own actions)


def test_ledger_append_named_as_the_slice_rituals_own_work_on_its_next_pass():
    _pin(
        "the driver writes no `## Slices` line itself; that append is the slice "
        "ritual's own work on its next pass, never the driver's",
        "The ledger append must be named explicitly as the slice ritual's own work on "
        "its next pass, not the driver's — this is a positive ownership statement, not "
        "an assertion that a forbidden string is absent.",
    )


def test_close_scoped_to_status_and_checkpoint_nothing_else():
    _pin(
        "This close writes exactly two things onto the vault: the parent's `done` "
        "status and the final `## Driver run` checkpoint",
        "The close must be scoped explicitly to the parent's status plus the "
        "checkpoint — naming the boundary of what the driver's close does write.",
    )


def test_close_section_contains_no_ledger_write_of_its_own():
    section = _close_section()
    assert "lore record update spec/" not in section, (
        "the slice-close step's own text must never instruct a write to the spec "
        "record — a `## Slices` ledger append can only land there, and that append "
        "belongs to ../slice/SKILL.md's next pass, never to the driver's own close"
    )


# --- contract item 4: the final checkpoint records the run as closed ------------------------
# (an interaction with 4.5's resume table, not merely that this sentence exists)


def test_close_writes_the_final_checkpoint_recording_slice_close():
    _pin(
        "write the final `## Driver run` checkpoint block recording `**Phase:** "
        "slice-close`",
        "The close must write the final checkpoint block recording the slice-close "
        "phase.",
    )


def test_close_checkpoint_names_the_resume_table_it_feeds():
    _pin(
        "4.5's resume table above already treats a block recording `slice-close` as a "
        "finished run with no phase after it to resume into",
        "The close checkpoint must name the interaction with 4.5's resume table "
        "explicitly — that a `slice-close` block is what makes 4.5 report the slice "
        "already closed rather than resuming a build, not merely that both texts exist "
        "independently.",
    )


def test_resume_table_still_carries_its_half_of_the_interaction():
    _pin(
        "A block recording `slice-close` names a finished run — there is no phase "
        "after it to resume into, and the driver reports the slice already closed "
        "rather than resuming anything",
        "4.5's resume table must still state its own half of the interaction: a "
        "`slice-close` block resumes as a report, not a rebuild.",
    )


def test_boundary_list_slice_close_entry_points_at_the_real_step():
    _pin(
        "once the slice is closed out at step 11 below, write the block recording "
        "`**Phase:** slice-close`",
        "The 4.5 boundary list's slice-close entry must point at the real step 11 "
        "rather than the old 'a later task' placeholder.",
    )
