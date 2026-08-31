"""The canonical slice/task vocabulary and quality bar — a conformance test.

Craft is gaining a per-slice delivery loop
([[spec/specs-are-delivered-one-vertical-slice-at-a-time]]), and its two units of
work — the vertical increment and the component-shaped unit beneath it — need one
place that defines them, so every skill's ritual text can point at the same wording
instead of drifting into independent paraphrases. `_shared/slice.md` is that place.

This task only ships the definition file and this test. Nothing else in craft reads
the file yet — later tasks in this slice point the existing prose at it.
"""

from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SHARED_SLICE = CRAFT / "skills" / "_shared" / "slice.md"
PLAN_SKILL = CRAFT / "skills" / "plan" / "SKILL.md"
TEMPLATE_TASK = CRAFT / "templates" / "task.md"
TEMPLATE_PLAN = CRAFT / "templates" / "plan.md"
PLANNER_AGENT = CRAFT / "agents" / "planner.md"
EXECUTE_SHARED = CRAFT / "skills" / "_shared" / "execute.md"
EXECUTE_SKILL = CRAFT / "skills" / "execute" / "SKILL.md"
NOTE_STORAGE = CRAFT / "skills" / "_shared" / "note-storage.md"
STATUS_OWNERSHIP = CRAFT / "skills" / "_shared" / "status-ownership.md"
EXECUTOR_AGENT = CRAFT / "agents" / "executor.md"
DRIFT_GATE_AGENT = CRAFT / "agents" / "drift-gate.md"
SIMPLIFIER_AGENT = CRAFT / "agents" / "simplifier.md"
ASSUMPTION_PROVER_AGENT = CRAFT / "agents" / "assumption-prover.md"
CODE_REVIEWER_AGENT = CRAFT / "agents" / "code-reviewer.md"
LOG_SIFTER_AGENT = CRAFT / "agents" / "log-sifter.md"
COUNCIL_SHARED = CRAFT / "skills" / "_shared" / "council.md"
REVIEW_SKILL = CRAFT / "skills" / "review" / "SKILL.md"


def test_shared_slice_definition_ships():
    assert SHARED_SLICE.exists(), (
        f"Expected the single-source slice/task definition at {SHARED_SLICE}"
    )


def test_slice_is_defined_as_a_vertical_increment():
    text = SHARED_SLICE.read_text()
    assert (
        "vertical increment" in text
    ), "_shared/slice.md must define 'slice' as a vertical increment"
    assert "observably more valuable to its consumer" in text, (
        "_shared/slice.md must state that a slice's completion leaves the system "
        "observably more valuable to its consumer"
    )


def test_task_is_defined_as_the_component_shaped_unit_beneath_a_slice():
    assert "component-shaped unit beneath a slice" in SHARED_SLICE.read_text(), (
        "_shared/slice.md must define 'task' as the component-shaped unit beneath a "
        "slice"
    )


def test_quality_bar_names_valuable_small_and_testable():
    text = SHARED_SLICE.read_text()
    for word in ("Valuable", "Small", "Testable"):
        assert word in text, f"_shared/slice.md must name {word!r} as part of the quality bar"


def test_quality_bar_rejects_independent_with_a_stated_reason():
    text = SHARED_SLICE.read_text()
    assert "Independent" in text, (
        "_shared/slice.md must mention 'Independent' explicitly to reject it"
    )
    assert "slices build on each other by design" in text, (
        "_shared/slice.md must state the reason Independent is rejected — a bare "
        "mention of the word does not pin the reason"
    )


def test_value_floor_reads_against_the_spec_own_consumer():
    text = SHARED_SLICE.read_text()
    assert "the spec's own consumer" in text, (
        "_shared/slice.md must read the value floor against the spec's own consumer, "
        "not an end user"
    )
    assert "all callers migrated" in text, (
        "_shared/slice.md must carry the all-callers-migrated contrast that "
        "distinguishes a cleared floor from an uncleared one"
    )


def test_selection_rule_is_smallest_next_and_denies_a_global_ranking():
    text = SHARED_SLICE.read_text()
    assert "smallest-next" in text, (
        "_shared/slice.md must state the selection rule as smallest-next above the "
        "value floor"
    )
    assert "not the rule" in text, (
        "_shared/slice.md must explicitly deny that a pre-committed global value "
        "ranking is the rule"
    )


def test_enabler_carve_out_names_all_three_constraints():
    text = SHARED_SLICE.read_text()
    assert "naming what it enables" in text, (
        "_shared/slice.md must require the enabler justification to name what it "
        "enables"
    )
    assert "cannot be folded into the slice needing it" in text, (
        "_shared/slice.md must require the justification to state why the enabler "
        "cannot be folded into the slice that needs it"
    )
    assert "the slice consuming it comes next" in text, (
        "_shared/slice.md must require that the slice consuming the enabler comes "
        "next"
    )


def test_enabler_justification_writes_no_record():
    assert "writes no record" in SHARED_SLICE.read_text(), (
        "_shared/slice.md must state that naming the consuming slice in the "
        "justification writes no record for that slice"
    )


# ---------------------------------------------------------------------------
# The planning surface — plan/SKILL.md, its two child templates, and
# planner.md's Planning-phase decomposition prose — points at _shared/slice.md
# rather than restating it, and names the plan's child unit "task".
# ---------------------------------------------------------------------------


def test_plan_skill_decomposition_section_names_the_child_unit_task():
    text = PLAN_SKILL.read_text()
    assert "### 7. Define Tasks" in text, (
        "plan/SKILL.md must re-head its decomposition step 'Define Tasks' — the "
        "plan's child unit is a task, not a slice"
    )
    start = text.index("### 7. Define Tasks")
    end = text.index("### 8. Write the Plan")
    section = text[start:end]
    assert "Break the feature into buildable tasks" in section, (
        "plan/SKILL.md's decomposition section must call the plan's child unit "
        "'task'"
    )
    assert "Tasks with unproven unknowns come first" in section, (
        "plan/SKILL.md's decomposition ordering rules must refer to 'tasks'"
    )


def test_plan_skill_uses_slice_for_the_observable_increment():
    text = PLAN_SKILL.read_text()
    assert "build it in slices" in text, (
        "plan/SKILL.md must still use 'slice' for the observable vertical "
        "increment it designs the whole feature toward"
    )


def test_plan_skill_references_shared_slice_rather_than_restating_the_bar():
    text = PLAN_SKILL.read_text()
    assert "_shared/slice.md" in text, (
        "plan/SKILL.md must reference _shared/slice.md for the quality bar and "
        "the value floor rather than restating them"
    )
    bar_wording = "Valuable, Small, Testable"
    carriers = sorted(
        p
        for p in CRAFT.rglob("*.md")
        if bar_wording in p.read_text(encoding="utf-8")
    )
    assert carriers == [SHARED_SLICE], (
        f"the quality bar's wording ({bar_wording!r}) must live in exactly one "
        f"shipped file (_shared/slice.md); found it in {carriers}"
    )


def test_task_template_describes_task_as_the_component_shaped_unit():
    assert "component-shaped unit beneath a slice" in TEMPLATE_TASK.read_text(), (
        "templates/task.md's comment block must describe a task as the "
        "component-shaped unit beneath a slice"
    )


def test_plan_template_comment_matches_task_template():
    assert "component-shaped unit beneath a slice" in TEMPLATE_PLAN.read_text(), (
        "templates/plan.md's comment block must describe a child task the same "
        "way templates/task.md does — the component-shaped unit beneath a slice"
    )


def test_planner_decomposition_section_names_the_child_unit_task():
    text = PLANNER_AGENT.read_text()
    assert "### 7. Define Tasks" in text, (
        "planner.md's Planning-phase decomposition step must re-head to 'Define "
        "Tasks'"
    )
    start = text.index("### 7. Define Tasks")
    end = text.index("### 8. Write the Plan")
    section = text[start:end]
    assert "Break the feature into buildable tasks" in section, (
        "planner.md's decomposition section must call the plan's child unit "
        "'task'"
    )
    assert "Tasks with unproven unknowns come first" in section, (
        "planner.md's decomposition ordering rules must refer to 'tasks'"
    )


def test_planner_uses_slice_for_the_observable_increment():
    text = PLANNER_AGENT.read_text()
    assert "build in slices" in text, (
        "planner.md must still use 'slice' for the observable vertical "
        "increment, the same sense plan/SKILL.md uses"
    )


# ---------------------------------------------------------------------------
# The execute surface — _shared/execute.md, execute/SKILL.md,
# _shared/note-storage.md, _shared/status-ownership.md, and the dispatched
# agents — names the per-child dispatched unit "task" and keeps "slice" only
# for the observable vertical increment (_shared/slice.md's sense).
# ---------------------------------------------------------------------------


def test_execute_shared_processes_the_plan_task_by_task():
    text = EXECUTE_SHARED.read_text()
    assert "builds an approved implementation plan task-by-task" in text, (
        "_shared/execute.md must process a plan's children task-by-task, not "
        "slice-by-slice — the plan itself is the slice; its children are tasks"
    )
    assert "### 1. Does this task have an unresolved unknown?" in text, (
        "_shared/execute.md's per-task loop must name the dispatched unit 'task'"
    )


def test_execute_shared_still_calls_a_standalone_task_its_own_slice():
    text = EXECUTE_SHARED.read_text()
    assert "a standalone\ntask record as its own single slice" in text, (
        "_shared/execute.md must keep calling a standalone task record its own "
        "single slice — that is the true vertical-increment sense of 'slice', "
        "unchanged by this rename"
    )


def test_execute_skill_processes_the_plan_task_by_task():
    text = EXECUTE_SKILL.read_text()
    assert "Execute a plan task-by-task" in text, (
        "execute/SKILL.md must mirror _shared/execute.md's task-by-task framing"
    )


def test_note_storage_describes_parent_slice_task_containing_child_tasks():
    text = NOTE_STORAGE.read_text()
    assert "each task is a child\n  `task` record" in text, (
        "note-storage.md must describe the plan's children as tasks, not slices"
    )


def test_note_storage_status_walk_names_the_same_transitions_in_new_words():
    text = NOTE_STORAGE.read_text()
    assert "the parent plan task's `ready → in-progress → done` lifecycle" in text, (
        "note-storage.md must still say only the parent plan task walks "
        "ready -> in-progress -> done"
    )
    assert "Child tasks\nunder a plan walk `ready → done`" in text, (
        "note-storage.md must restate 'child slices go straight ready -> done' "
        "as 'child tasks', without changing which record walks which statuses"
    )


def test_status_ownership_names_the_same_writer_in_new_words():
    text = STATUS_OWNERSHIP.read_text()
    assert (
        "child tasks under a plan walk\n  `ready → done` and never take it" in text
    ), (
        "status-ownership.md must restate the child-slice carve-out as "
        "'child tasks' without changing the writer or the transition"
    )


def test_drift_gate_names_its_unit_of_work_task():
    text = DRIFT_GATE_AGENT.read_text()
    assert "Per-task conformance gate for the execute loop" in text, (
        "drift-gate.md must name its unit of work 'task', consistent with "
        "_shared/slice.md"
    )
    assert "next-task readiness: N/A — standalone leaf" in text, (
        "drift-gate.md's check 3 must be renamed 'next-task readiness'"
    )


def test_executor_names_its_unit_of_work_task():
    text = EXECUTOR_AGENT.read_text()
    assert "TDD implementer for a single task in the execute loop" in text, (
        "executor.md must name its unit of work 'task', consistent with "
        "_shared/slice.md"
    )
    assert "no earlier or next tasks" in text, (
        "executor.md must say a standalone leaf has no earlier or next tasks"
    )


def test_simplifier_names_its_unit_of_work_task():
    text = SIMPLIFIER_AGENT.read_text()
    assert "commit your change **separately from the task commits**" in text, (
        "simplifier.md must commit separately from task commits, not slice "
        "commits"
    )


def test_assumption_prover_names_its_unit_of_work_task():
    text = ASSUMPTION_PROVER_AGENT.read_text()
    assert "when a task depends on an unresolved unknown" in text, (
        "assumption-prover.md must name its unit of work 'task', consistent "
        "with _shared/slice.md"
    )


def test_code_reviewer_names_drift_gates_unit_of_work_task():
    text = CODE_REVIEWER_AGENT.read_text()
    assert "Per-task conformance checks during execute" in text, (
        "code-reviewer.md's bad-fits list must call drift-gate's job "
        "per-task, not per-slice"
    )


def test_log_sifter_keeps_the_ordinary_english_sense_of_slice():
    text = LOG_SIFTER_AGENT.read_text()
    assert "extracts the relevant slices" in text, (
        "log-sifter.md's 'relevant slices' of a log is ordinary English, not "
        "craft's work-unit vocabulary — it must be left alone"
    )


# ---------------------------------------------------------------------------
# The remaining prose surface — _shared/council.md's plan-review finding
# examples and review/SKILL.md's own unit of work — names the plan-altitude
# component unit "task", consistent with _shared/slice.md.
# ---------------------------------------------------------------------------


def test_council_shared_plan_bars_name_the_component_unit_task():
    text = COUNCIL_SHARED.read_text()
    assert "Task ordering creates a dependency that can't be tested" in text, (
        "_shared/council.md's Builder plan-review bar must name the plan's "
        "component unit 'task', consistent with _shared/slice.md"
    )
    assert (
        "Producer task's contract isn't proven by tests but a consumer task "
        "depends on it" in text
    ), (
        "_shared/council.md's Builder plan-review bar must name both the "
        "producer and consumer unit 'task'"
    )
    assert "A task has no test contract, OR test contract is vacuous" in text, (
        "_shared/council.md's Reliability plan-review bar must name the "
        "plan's component unit 'task'"
    )
    assert (
        "A task does irreversible work without dry-run / preview / staged "
        "rollout" in text
    ), (
        "_shared/council.md's Reliability plan-review bar must name the "
        "plan's component unit 'task'"
    )


def test_review_skill_names_its_unit_of_work_task():
    text = REVIEW_SKILL.read_text()
    assert "Per-task conformance during execute is `drift-gate`'s job" in text, (
        "review/SKILL.md must name the unit drift-gate conforms 'task', "
        "consistent with _shared/slice.md"
    )
    assert "**Execute (task-by-task subagent development):**" in text, (
        "review/SKILL.md's Integration with Workflows section must describe "
        "execute as task-by-task, not slice-by-slice"
    )
