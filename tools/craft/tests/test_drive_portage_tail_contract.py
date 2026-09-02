"""`/craft:drive`'s PR tail.

This task ships the driver's PR tail: dispatching portage's `updater` and then `monitor`
from the driver session itself — never nested inside another subagent, which loses the
notification channel — deriving `group_toml_path` from camp's own group config rather than a
ranger artifact, pre-creating the outcome file's parent directory before dispatching `monitor`,
polling the outcome file against the driver's own deadline rather than waiting on the dispatch
notification, and mapping portage's four terminal tokens (plus the empty-file case) explicitly
with no default or fall-through branch. `MERGED`, `READY <reason>`, and
`STOPPED auto_merge disabled` all close the slice; every other `STOPPED <reason>`, `BLOCKED
<reason>`, and an empty or missing outcome file all escalate. It defines the PR tail and its
token map; the slice close it hands off to is pinned by `test_drive_slice_close_contract.py`.

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


def test_pr_tail_never_nested_inside_a_subagent():
    _pin(
        "never nested inside any subagent, which would "
        "lose the notification channel",
        "The PR tail dispatch must state it is never nested inside another subagent, "
        "since that would lose the notification channel.",
    )


def test_monitor_dispatched_in_the_background_from_the_top_level():
    _pin(
        "Then dispatch `monitor` **in the background, from this top-level session**",
        "monitor must be dispatched in the background from the top-level driver session.",
    )


def test_monitor_dispatch_never_nested_inside_a_subagent():
    _pin(
        "never nested inside any subagent, matching this ritual's "
        "top-level-only dispatch rule",
        "The monitor dispatch must explicitly rule out nesting inside a subagent.",
    )


def test_updater_dispatched_synchronously_first():
    _pin(
        "Dispatch `updater` first, synchronously, from this session",
        "updater must be dispatched synchronously, before monitor.",
    )


# --- updater is dispatched in `create` mode, the mode that actually opens a PR ----------


def test_updater_dispatched_with_create_mode():
    _pin(
        "passing `mode: create`",
        "The build phase's own close pushes a branch but opens no PR, so the PR "
        "tail must dispatch updater with `mode: create` — the mode that opens "
        "one — never `mode: update`, which requires a PR to already exist.",
    )


def test_create_mode_chosen_because_it_opens_the_pr_not_because_unpushed():
    _pin(
        "`create` is selected because it is the mode that opens the PR, not "
        "because the branch is unpushed",
        "The ritual must state why `create` is correct even though the branch "
        "may already be pushed by the build phase's own close.",
    )


# --- an updater preflight failure escalates rather than dispatching monitor against ------
# --- pr_pairs that was never returned ----------------------------------------------------


def test_updater_preflight_failure_escalates_under_named_trigger():
    _pin(
        "escalate under the `updater-preflight-failed` trigger",
        "An `updater` preflight failure must escalate under a named trigger "
        "from the closed vocabulary, rather than falling through to a "
        "`monitor` dispatch against `pr_pairs` that was never returned.",
    )


def test_updater_preflight_failure_trigger_declared_in_vocabulary():
    _pin(
        "**`updater-preflight-failed`** — the PR tail's `updater` dispatch",
        "The `updater-preflight-failed` trigger must be declared in the "
        "closed trigger vocabulary with its full triggering condition named.",
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


def test_step_ten_names_its_own_manifest_read():
    _pin(
        "Read `manifest.json` at the derived camp workspace root again here — a resume "
        "re-entering directly at this phase skips step 5 entirely",
        "Step 10 must name its own read of manifest.json rather than sourcing the "
        "camp group name from 'the camp manifest read at step 5', since a resume "
        "re-entering at pr-tail skips step 5 and never performs that read.",
    )


def test_group_toml_path_formula_uses_camp_config_dir_convention():
    _pin(
        'GROUP_TOML_PATH="${CAMP_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/camp}/groups/<group>.toml"',
        "The group_toml_path formula must follow camp's own config_dir/groups/<name>.toml "
        "convention, honoring the per-app override before the XDG default.",
    )


def test_group_name_shape_checked_before_substitution_into_group_toml_path():
    _pin(
        "Validate the group name against the safe-value shape step 1 states "
        "(`^[A-Za-z0-9._/-]+$`) before substituting it below",
        "The group name read from manifest.json is substituted straight into a "
        "filesystem path — it must be shape-checked first, matching every other "
        "substitution site in this ritual, rather than skipped as the one exception.",
    )


def test_group_name_shape_check_refuses_rather_than_substituting_on_mismatch():
    _pin(
        "A value that fails the shape check is never substituted; refuse loudly "
        "and stop",
        "A group name failing the shape check must produce a loud refusal, "
        "matching step 1's own refuse-loudly rule, not a silent substitution.",
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
    "stopped-auto-merge": "`STOPPED <reason>` where `<reason>` contains `auto_merge` — "
    "closes the slice; the stacked-slice success path, not a failure.",
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


def test_stopped_auto_merge_closes_the_slice():
    _pin(
        "`STOPPED <reason>` where `<reason>` contains `auto_merge` — closes the slice; "
        "the stacked-slice success path, not a failure.",
        "A STOPPED reason naming auto_merge must map to closing the slice, as the "
        "stacked-slice success path — never to escalation.",
    )


def test_every_other_stopped_reason_escalates():
    _pin(
        "Every other `STOPPED <reason>` — escalates under the `portage-stopped` trigger",
        "Every STOPPED reason other than auto_merge disabled must escalate under "
        "portage-stopped.",
    )


# --- the auto_merge match is a substring test, never prefix or whole-string equality ---
# --- against portage's free-text STOPPED <reason> grammar ------------------------------


def test_auto_merge_matched_by_substring_not_prefix_or_whole_string_equality():
    _pin(
        "Match on `auto_merge` appearing anywhere in the reason text, never on the "
        "whole-string or prefix literal `STOPPED auto_merge disabled`",
        "The stacked-slice success case must be matched by a substring test for "
        "`auto_merge` anywhere in the reason — never by a prefix or whole-string "
        "match against the literal `STOPPED auto_merge disabled`, which monitor "
        "documents only as an example and does not actually emit.",
    )


def test_states_why_prefix_or_whole_string_equality_is_wrong_here():
    _pin(
        "no prefix of `auto_merge disabled` matches that text, so a prefix or "
        "whole-string match would silently misclassify every stacked-slice "
        "success as an escalation",
        "The ritual must state explicitly why a prefix or whole-string match "
        "is wrong here — monitor's real reason text, `auto_merge is unset/"
        "false`, shares no prefix with `auto_merge disabled`, so that rule "
        "would escalate every stacked-slice success instead of closing it.",
    )


def test_cites_monitor_documented_example_string():
    _pin(
        "monitor documents that string only as an example in its token grammar "
        "(`tools/portage/plugins/portage/agents/monitor.md:90`)",
        "The match-rule rationale must cite where monitor documents "
        "`STOPPED auto_merge disabled` as an example, not a fixed literal.",
    )


def test_cites_monitor_actual_emitted_reason_text():
    _pin(
        "the reason text it actually emits is `STOPPED: all PRs are ready to merge, "
        "but auto_merge is unset/false",
        "The match-rule rationale must cite the actual reason text monitor emits, "
        "which shares no prefix with the documented example.",
    )


# --- a malformed outcome line (none of the four tokens, or a bare READY) is mapped -----
# --- to an escalation, so the map's exhaustiveness claim is actually true --------------


def test_malformed_outcome_line_escalates():
    _pin(
        "A line naming none of `MERGED` / `READY <reason>` / `STOPPED <reason>` / "
        "`BLOCKED <reason>`, or a `READY` with no argument — escalates under the "
        "`portage-tail-malformed` trigger",
        "A malformed outcome line — naming none of the four tokens, or a bare "
        "`READY` with no argument, both of which portage's own parser refuses "
        "— must be mapped to an escalation, not fall through the exhaustive "
        "token map unhandled.",
    )


def test_portage_tail_malformed_trigger_declared_in_vocabulary():
    _pin(
        "**`portage-tail-malformed`**",
        "The `portage-tail-malformed` trigger must be declared in the closed "
        "trigger vocabulary.",
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
        "this phase names that outcome and defers the close mechanics to slice close — "
        "step 11 below",
        "Every branch that closes the slice must name that outcome and defer the close "
        "mechanics to slice close, pointing at the real step 11 rather than the old "
        "'a later task' placeholder.",
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
