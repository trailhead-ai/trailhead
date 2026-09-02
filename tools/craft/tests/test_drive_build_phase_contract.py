"""`/craft:drive`'s build phase.

This task ships the driver's build phase: a new `craft:driver-worker` agent that runs
craft's shared execute procedure in its unattended mode against the slice parent's child
task graph, in its own context, and the driver-side dispatch that drives it — a pinned
six-value dispatch prompt, a background top-level dispatch bounded by a liveness deadline,
a read of the worker's result from its outcome file (never its reply), a missing/empty
outcome file read as a crash, and an escalation under `worker-stalled` with no retry on
any non-success outcome. It defines the build phase and the worker agent that phase
dispatches; the PR tail it hands off to is pinned by `test_drive_portage_tail_contract.py`.

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
DRIVER_WORKER = CRAFT / "agents" / "driver-worker.md"


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


def _frontmatter(path: Path) -> str:
    text = path.read_text()
    assert text.startswith("---\n"), f"{path.name} must open with a frontmatter block"
    end = text.find("\n---", 3)
    assert end > 0, f"{path.name} frontmatter block is not closed"
    return text[3:end]


def _tools(path: Path) -> list[str]:
    for line in _frontmatter(path).splitlines():
        if line.strip().startswith("tools:"):
            return [t.strip() for t in line.split(":", 1)[1].split(",") if t.strip()]
    return []


# --- the agent ships and is registrable / generic (mechanically re-checked here since ---
# --- this task is what introduces the file the parametrized suites discover) ------------


def test_driver_worker_agent_ships():
    assert DRIVER_WORKER.exists(), f"Expected the craft:driver-worker agent at {DRIVER_WORKER}"


# --- the Agent tool grant is a hard, mutation-checked requirement -----------------------


def test_driver_worker_carries_an_agent_tool_grant():
    tools = _tools(DRIVER_WORKER)
    assert "Agent" in tools, (
        "craft:driver-worker must carry an `Agent` tool grant — it genuinely dispatches "
        f"assumption-prover, executor, and drift-gate. Got tools: {tools!r}"
    )


# --- the agent states why it needs Agent, and never cites ranger:execute as nesting proof ---


def test_agent_states_it_genuinely_dispatches_subagents():
    _pin(
        "You genuinely dispatch subagents — unlike `ranger:execute`.",
        "The agent must state plainly that it dispatches subagents, unlike ranger:execute.",
        path=DRIVER_WORKER,
    )


def test_agent_cites_ranger_execute_for_shape_only():
    _pin(
        "`ranger:execute` is precedent for this agent's **shape**",
        "The agent must cite ranger:execute as shape precedent only.",
        path=DRIVER_WORKER,
    )


def test_agent_forbids_citing_ranger_execute_as_nesting_evidence():
    _pin(
        "Never cite it as evidence that nesting works",
        "The agent must explicitly forbid citing ranger:execute as evidence nesting works, "
        "since it carries no Agent tool at all and cannot nest.",
        path=DRIVER_WORKER,
    )


def test_agent_states_nested_dispatches_are_synchronous():
    _pin(
        "Every dispatch you make is synchronous — never `run_in_background`.",
        "The agent must pin that its own nested dispatches are always synchronous, "
        "never backgrounded.",
        path=DRIVER_WORKER,
    )


def test_agent_warns_against_backgrounding_out_of_caution():
    _pin(
        "Do not background any of them out of caution",
        "The agent must warn a later implementer against backgrounding its nested "
        "dispatches out of caution, since that would reintroduce the notification loss.",
        path=DRIVER_WORKER,
    )


# --- the agent writes exactly one token to the outcome file -----------------------------


def test_agent_writes_exactly_one_token():
    _pin(
        "Your last action is to write **exactly one token** to the outcome file",
        "The agent must write exactly one token to its outcome file and nothing else.",
        path=DRIVER_WORKER,
    )


def test_agent_treats_missing_file_as_crash():
    _pin(
        "A missing or empty outcome file reads to",
        "The agent must state that a missing or empty outcome file reads as a crash.",
        path=DRIVER_WORKER,
    )


# --- the agent's run ends at the procedure's close, never the portage tail --------------


def test_agent_run_ends_at_procedure_close_not_portage():
    _pin(
        "Your run ends where the shared procedure's own close phase ends — a pushed "
        "branch — never a merge",
        "The agent's run must end at the shared procedure's close, not extend into "
        "portage's tail.",
        path=DRIVER_WORKER,
    )


# --- SKILL.md: the build phase dispatch names the six values and nothing else -----------


_DISPATCH_LINES = {
    "record-id": "Record id: <slice-parent-task-id>",
    "execute-procedure": "Execute procedure: <path-to-_shared-execute.md>",
    "templates-root": "Templates root: <path-to-templates-root>",
    "elected-vault": "Elected vault: <elected-vault>",
    "workspace-path": "Workspace path: <workspace-path>",
    "outcome-file": "Outcome file: <outcome-file-path>",
}


def _build_dispatch_block() -> str:
    """The six-value build-phase dispatch's own fenced block — scoped so a pin against
    it cannot be vacuously satisfied by the plan phase's identical-looking dispatch
    lines (e.g. `Outcome file: <outcome-file-path>` appears in both blocks)."""
    text = DRIVE_SKILL.read_text()
    match = re.search(
        r"```text\nRecord id: <slice-parent-task-id>\n.*?\n```", text, re.DOTALL
    )
    assert match, "expected the build-phase's six-value fenced dispatch block"
    return match.group(0)


@pytest.mark.parametrize("label,line", list(_DISPATCH_LINES.items()), ids=list(_DISPATCH_LINES))
def test_build_dispatch_names_each_value(label, line):
    _pin(
        line,
        f"The build-phase dispatch prompt must name the {label} value on its own line.",
    )


@pytest.mark.parametrize("label,line", list(_DISPATCH_LINES.items()), ids=list(_DISPATCH_LINES))
def test_build_dispatch_block_itself_names_each_value(label, line):
    block = _build_dispatch_block()
    assert line in block, (
        f"the build-phase's own six-value dispatch block is missing the {label} line "
        f"({line!r}) — pinning this within the block (not just anywhere in the file) "
        "is what stops a deletion of this line from being masked by the plan phase's "
        "identical-looking dispatch lines elsewhere in the document"
    )


def test_build_dispatch_passes_nothing_else_about_the_slice():
    _pin(
        "Pass it exactly six values and nothing else about the slice",
        "The build-phase dispatch must carry exactly the six named values and nothing else "
        "about the slice.",
    )


# --- the dispatch is backgrounded from the top level, never nested ----------------------


def test_build_dispatch_runs_in_the_background_from_the_top_level():
    _pin(
        "Dispatch it **in the background**, from this top-level session",
        "The driver's own dispatch of craft:driver-worker must run in the background, "
        "since it is a top-level dispatch, not a nested one.",
    )


# --- the dispatch carries a liveness deadline bounding the worker's own run -------------


def test_dispatch_carries_a_liveness_deadline():
    _pin(
        "The dispatch carries a liveness deadline.",
        "The build-phase dispatch must carry a liveness deadline rather than waiting "
        "indefinitely.",
    )


def test_deadline_bounds_the_workers_own_run_not_the_portage_tail():
    _pin(
        "It bounds the worker's own run, which ends at the shared procedure's close",
        "The liveness deadline must be stated to bound the worker's own run — ending at "
        "the shared procedure's close, not the portage tail.",
    )


def test_deadline_excludes_the_portage_tail():
    _pin(
        "it does not cover the portage tail, which is external to this dispatch",
        "The deadline must be stated to exclude the portage tail explicitly.",
    )


# --- expiry and a missing/empty outcome file both escalate under worker-stalled ---------


def test_deadline_expiry_escalates_under_worker_stalled_with_no_retry():
    _pin(
        "treat the worker as crashed, and escalate under the `worker-stalled` trigger with "
        "no retry",
        "A liveness-deadline expiry must escalate under the worker-stalled trigger, with "
        "no retry.",
    )


def test_missing_or_empty_outcome_file_is_read_as_a_crash():
    _pin(
        "A missing or empty outcome file is read as a **crash**, not as still-running",
        "A missing or empty outcome file must be read as a crash, not as still running.",
    )


def test_missing_file_shares_the_worker_stalled_trigger_with_deadline_expiry():
    _pin(
        "escalates under the same `worker-stalled` trigger a deadline expiry does",
        "A missing/empty outcome file must escalate under the same trigger a deadline "
        "expiry does — the two are the same failure observed by different clocks.",
    )


# --- the driver reads the result from the outcome file, never the agent's reply --------


def test_build_result_read_from_outcome_file_never_the_reply():
    _pin(
        "matching the plan phase's own worker-channel rule at step 7",
        "The build phase must read its result from the outcome file, matching the plan "
        "phase's own rule against reading a subagent's reply.",
    )


# --- any non-success token escalates with no retry --------------------------------------


def test_any_non_success_token_escalates_with_no_retry():
    _pin(
        "Anything else — `BLOCKED`, `NEEDS_CONTEXT`, any other token, a missing or empty "
        "outcome file, or a deadline expiry — escalates under the `worker-stalled` trigger",
        "Any non-success token, or a missing/empty file, or a deadline expiry must "
        "escalate under worker-stalled with no retry.",
    )


# --- the phase checkpoint is written both before and after the dispatch ----------------


def test_checkpoint_written_before_and_after_the_dispatch():
    _pin(
        "The checkpoint at this boundary is written before and after the dispatch",
        "The phase checkpoint must be written both before and after the build dispatch, "
        "so a crash inside the build phase is distinguishable from a crash before it.",
    )


def test_before_checkpoint_is_the_plan_phase_block():
    _pin(
        "the `## Driver run` block recording `**Phase:** plan` already written at step 8 "
        "is the before-dispatch record",
        "The before-dispatch checkpoint must be identified as the plan-phase block "
        "already written at step 8.",
    )


def test_after_checkpoint_records_phase_build():
    _pin(
        "writing the block recording `**Phase:** build` here is the after-dispatch record",
        "The after-dispatch checkpoint must be identified as the build-phase block "
        "written once the dispatch returns DONE.",
    )


# --- the 4.5 resume table points the build boundary at the real dispatch, not a stub ---


def test_resume_table_build_entry_points_at_step_nine():
    _pin(
        "once `craft:driver-worker`'s dispatch (step 9 below) returns `DONE`, write the "
        "block recording `**Phase:** build`",
        "The 4.5 resume table's build-boundary entry must point at the real build-phase "
        "step rather than the old 'a later task' placeholder.",
    )


# --- the trigger vocabulary's worker-stalled entry names the real condition ------------


def test_worker_stalled_vocabulary_entry_names_the_real_condition():
    _pin(
        "the build dispatch (step 9 above) passes its liveness deadline with no progress "
        "signal, or its outcome file comes back missing, empty, or naming anything other "
        "than `DONE`",
        "The worker-stalled vocabulary entry must name both triggering conditions — "
        "deadline expiry and a missing/empty/non-DONE outcome file.",
    )
