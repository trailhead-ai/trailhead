"""`/craft:drive`'s PR tail.

This task ships the driver's PR tail: dispatching portage's `updater` and then `monitor`
from the driver session itself — never nested inside another subagent, which loses the
notification channel — deriving `group_toml_path` from camp's own group config rather than a
ranger artifact, pre-creating the outcome file's parent directory before dispatching `monitor`,
polling the outcome file against the driver's own deadline rather than waiting on the dispatch
notification, and mapping portage's four terminal tokens (plus the empty-file case) explicitly
with no default or fall-through branch. `MERGED`, `READY <reason>`, and
`STOPPED auto_merge disabled` all close the slice; every other `STOPPED <reason>`, `BLOCKED
<reason>`, and an empty or missing outcome file all escalate. It does not build slice close
itself (a later task against this same file); it defines the PR tail and its token map.

Pinned here, using the wrap-aware `_pin` helper mirrored from `test_drive_build_phase_contract.py`
(itself mirrored from `test_drive_plan_phase_contract.py`, `test_drive_escalation_contract.py`,
`test_drive_resume_contract.py`, `test_drive_skill_contract.py`, `test_execute_mode_contract.py`,
and ranger's `tests/test_sweep_contract.py`): every pinned span is asserted as a contiguous
substring **within one physical line**, so a markdown rewrap that shifts a line break fails
loudly as a wrap issue rather than reading as "phrase missing".
"""

from __future__ import annotations

from pathlib import Path

import pytest

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
DRIVE_SKILL = CRAFT / "skills" / "drive" / "SKILL.md"


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


# --- the dispatch happens from the driver session itself, never nested -----------------


def test_pr_tail_dispatched_from_the_driver_session_itself():
    _pin(
        "hand the branch to portage from this session itself",
        "The PR tail must be dispatched from the driver's own session, never delegated.",
    )


def test_pr_tail_never_nested_inside_driver_worker():
    _pin(
        "never nested inside `craft:driver-worker` or any other subagent, which would "
        "lose the notification channel",
        "The PR tail dispatch must state it is never nested inside another subagent, "
        "since that would lose the notification channel.",
    )


def test_monitor_dispatched_in_the_background_from_the_top_level():
    _pin(
        "Then dispatch `monitor` **in the background, from this top-level session**",
        "monitor must be dispatched in the background from the top-level driver session.",
    )


def test_monitor_dispatch_never_nested_inside_driver_worker():
    _pin(
        "never nested inside `craft:driver-worker`, matching the build phase's own "
        "top-level-only dispatch rule at step 9",
        "The monitor dispatch must explicitly rule out nesting inside craft:driver-worker.",
    )


def test_updater_dispatched_synchronously_first():
    _pin(
        "Dispatch `updater` first, synchronously, from this session",
        "updater must be dispatched synchronously, before monitor.",
    )


# --- the driver never merges, orders a merge, or reverts --------------------------------


def test_driver_responsibility_ends_at_green():
    _pin(
        "The driver's responsibility ends at green: it maps portage's terminal tokens "
        "and hands off; it never merges, never orders a merge, and never reverts.",
        "The PR tail must state the driver never merges, orders a merge, or reverts.",
    )


# --- group_toml_path is derived from camp's own group config, not a ranger artifact -----


def test_group_toml_path_derived_from_camps_own_group_config():
    _pin(
        "Derive `group_toml_path` from camp's own group config, never from a ranger artifact.",
        "group_toml_path must be derived from camp's own group config, never a ranger "
        "artifact.",
    )


def test_group_toml_path_formula_uses_camp_config_dir_convention():
    _pin(
        'GROUP_TOML_PATH="${CAMP_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/camp}/groups/<group>.toml"',
        "The group_toml_path formula must follow camp's own config_dir/groups/<name>.toml "
        "convention, honoring the per-app override before the XDG default.",
    )


# --- the outcome file's parent directory is pre-created before dispatching monitor ------


def test_outcome_dir_precreated_before_dispatching_monitor():
    _pin(
        "Pre-create the outcome file's parent directory before dispatching `monitor`.",
        "The outcome file's parent directory must be pre-created before monitor is "
        "dispatched — monitor does not create it itself.",
    )


def test_missing_outcome_dir_would_silently_become_empty_file_escalation():
    _pin(
        "a silent way to turn every run into an empty-file escalation",
        "The ritual must state why pre-creating the outcome directory matters — a missing "
        "directory would silently turn every run into an empty-file escalation.",
    )


def test_outcome_dir_precreate_gets_its_own_explicit_step():
    _pin(
        "so this gets its own explicit step rather than riding along with anything else",
        "The outcome-directory pre-create must be called out as its own explicit step, "
        "not folded silently into another one.",
    )


# --- the outcome file is polled against the driver's own deadline, never the notification ---


def test_outcome_file_polled_against_the_drivers_own_deadline():
    _pin(
        "Poll the outcome file against the driver's own deadline, never wait on the "
        "dispatch notification.",
        "The driver must poll the outcome file against its own deadline rather than "
        "waiting on the dispatch notification.",
    )


def test_outcome_file_is_the_documented_contract_not_the_notification():
    _pin(
        "The file is the documented contract for an unattended caller; the notification "
        "is not",
        "The ritual must state the file, not the notification, is the documented contract "
        "for an unattended caller.",
    )


def test_polling_precedent_cites_ranger_drain_section_six():
    _pin(
        "`tools/ranger/plugins/ranger/skills/execute/SKILL.md`, section 6",
        "The polling rule must cite ranger's drain precedent at section 6.",
    )


def test_outcome_read_from_file_never_the_reply():
    _pin(
        "Read the outcome as exactly one line from the file, never from `monitor`'s reply",
        "The PR tail must read monitor's outcome from the file, never from its reply.",
    )


# --- the four-token map is exhaustive: no default or fall-through branch ---------------


def test_token_map_states_no_default_or_fallthrough():
    _pin(
        "The four-token map is exhaustive — no default or fall-through branch handles "
        "anything else",
        "The token map must be stated as exhaustive, with no default or fall-through "
        "branch.",
    )


_TOKEN_MAP_CASES = {
    "merged": "`MERGED` — closes the slice; the token takes no argument.",
    "ready": "`READY <reason>` — closes the slice.",
    "stopped-auto-merge-disabled": "`STOPPED auto_merge disabled` — closes the slice; the "
    "stacked-slice success path, not a failure.",
    "stopped-other": "Every other `STOPPED <reason>` — escalates under the "
    "`portage-stopped` trigger, following the escalation contract below, with no retry.",
    "blocked": "`BLOCKED <reason>` — escalates under the `portage-blocked` trigger, "
    "following the escalation contract below, with no retry.",
    "empty-file": "An empty or missing outcome file — escalates under the "
    "`portage-tail-stalled` trigger, following the escalation contract below, with no "
    "retry.",
}


@pytest.mark.parametrize("case,line", list(_TOKEN_MAP_CASES.items()), ids=list(_TOKEN_MAP_CASES))
def test_token_map_case_has_an_explicit_mapping(case, line):
    _pin(
        line,
        f"the token-map case {case!r} must have an explicit mapping in the PR tail's "
        "token list, with no fall-through to a default.",
    )


# --- STOPPED auto_merge disabled closes; every other STOPPED escalates -----------------
# --- (the single distinction carrying the whole stacked-slice path) --------------------


def test_stopped_auto_merge_disabled_closes_the_slice():
    _pin(
        "`STOPPED auto_merge disabled` — closes the slice; the stacked-slice success "
        "path, not a failure.",
        "STOPPED auto_merge disabled must map to closing the slice, as the stacked-slice "
        "success path — never to escalation.",
    )


def test_every_other_stopped_reason_escalates():
    _pin(
        "Every other `STOPPED <reason>` — escalates under the `portage-stopped` trigger",
        "Every STOPPED reason other than auto_merge disabled must escalate under "
        "portage-stopped.",
    )


# --- the trigger vocabulary carries the three new PR-tail triggers ----------------------


_NEW_TRIGGERS = {
    "portage-blocked": "**`portage-blocked`** — the PR tail's `monitor` outcome (step 10 "
    "above) comes back `BLOCKED <reason>`.",
    "portage-stopped": "**`portage-stopped`** — the PR tail's `monitor` outcome (step 10 "
    "above) comes back `STOPPED <reason>` for any reason other than `auto_merge disabled`.",
    "portage-tail-stalled": "**`portage-tail-stalled`** — the PR tail's `monitor` outcome "
    "file (step 10 above) comes back missing or empty.",
}


@pytest.mark.parametrize("trigger,line", list(_NEW_TRIGGERS.items()), ids=list(_NEW_TRIGGERS))
def test_new_trigger_declared_in_vocabulary(trigger, line):
    _pin(
        line,
        f"the `{trigger}` trigger must be declared in the vocabulary list with its full "
        "triggering condition named.",
    )


# --- no merge, merge-order, or revert affordance ----------------------------------------


def test_no_merge_affordance_in_pr_tail():
    _pin(
        "it never merges, never orders a merge, and never reverts.",
        "The PR tail must carry no merge, merge-order, or revert affordance.",
    )


# --- the phase checkpoint is written at this boundary -----------------------------------


def test_checkpoint_written_at_pr_tail_boundary():
    _pin(
        "write the `## Driver run` checkpoint block recording `**Phase:** pr-tail` before "
        "the slice-close mechanics run",
        "The PR tail must write its own `## Driver run` checkpoint recording `pr-tail` "
        "before the slice-close mechanics run.",
    )


def test_checkpoint_prevents_tail_crash_resuming_as_pre_build_crash():
    _pin(
        "so a crash in the tail does not resume as a crash before the build",
        "The checkpoint's purpose must be stated: a crash in the tail must not resume as "
        "a crash before the build.",
    )


# --- slice close mechanics are named as the outcome and deferred, not built here --------


def test_close_the_slice_outcome_is_named_and_deferred():
    _pin(
        "this phase names that outcome and defers the close mechanics to slice close, a "
        "later task against this same file",
        "Every branch that closes the slice must name that outcome and defer the close "
        "mechanics to the later slice-close task.",
    )


# --- the resume table's pr-tail entry points at the real step 10, not a stub ------------


def test_resume_table_pr_tail_entry_points_at_step_ten():
    _pin(
        "once the PR tail phase (step 10 below) maps portage's outcome, write the block "
        "recording `**Phase:** pr-tail`",
        "The 4.5 resume table's pr-tail entry must point at the real PR-tail step rather "
        "than the old 'a later task' placeholder.",
    )


# --- step 9's DONE branch points at the real step 10, not a stub -----------------------


def test_build_done_branch_continues_into_step_ten():
    _pin(
        "Then continue into the PR tail — step 10 below.",
        "The build phase's DONE branch must continue into the real PR-tail step rather "
        "than the old placeholder language.",
    )
