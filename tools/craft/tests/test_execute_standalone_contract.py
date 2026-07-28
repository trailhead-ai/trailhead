"""Execute's Loop preamble — branching on task shape (standalone vs plan).

Execute's Loop was written for a parent-with-children plan graph and dead-ended on a
standalone (childless, parentless) task. This file locks the prose contract that fixes
that: a task-shape branch at the top of the Loop, and the adaptations the end pipeline
needs when the whole change is a single standalone task rather than a parent's children.

  - **Detection is mechanical, not a vibe.** Standalone iff `lore task graph <name>`
    renders exactly one line AND the record's sidecar carries no `parent` edge.
  - **The ambiguous case is never guessed at.** A single-line render WITH a `parent`
    edge present is a suspected mis-wired edge — execute reports it and stops, citing
    the lesson that graph edges require bare task names.
  - **Dispatch is marker-gated.** A standalone `ready` node without the `(runnable)`
    marker has an unmet `depends-on` edge and must not be dispatched blind.
  - **Refine runs inline, not as a skill hop.** A standalone `open` task routes through
    `_shared/refine.md` — the same single source of truth `/craft:refine` wraps.
  - **Simplify is opt-in on a single leaf.** It skips by default and is named explicitly
    when it does not fire — "skipped — single leaf, no trigger" is a discovery handle,
    not a summary of prose.

`skills/_shared/` is a reference doc, not a skill — see test_refine_contract.py for its
own contract. This file only locks execute/SKILL.md's side of the integration.
"""

from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
EXECUTE_SKILL = CRAFT / "skills" / "execute" / "SKILL.md"
SHARED_REFINE = CRAFT / "skills" / "_shared" / "refine.md"

# Mirrors test_refine_contract.py's ESCALATION_HEADING — kept as its own literal here
# rather than imported, so this file's collection does not depend on collection order.
ESCALATION_HEADING = "## Refine — unresolved"

MISWIRED_LESSON_LINK = "[[lesson/lore-task-graph-parent-depends-on-require-bare-task-names]]"


def _execute_text() -> str:
    assert EXECUTE_SKILL.exists(), f"Expected execute/SKILL.md at {EXECUTE_SKILL}"
    return EXECUTE_SKILL.read_text()


# --- standalone detection ---


def test_detection_requires_exactly_one_line_render():
    assert "renders exactly one line" in _execute_text(), (
        "execute/SKILL.md must state the standalone detection's first condition: "
        "`lore task graph <name>` renders exactly one line for the task"
    )


def test_detection_requires_no_parent_edge():
    assert "no `parent` edge" in _execute_text(), (
        "execute/SKILL.md must state the standalone detection's second condition: "
        "the record's sidecar carries no `parent` edge — a single-line render alone is "
        "ambiguous (a child slice of a live plan also renders one line)"
    )


# --- the ambiguous third case: report, never silently classify ---


def test_ambiguous_case_is_never_classified_silently():
    assert "never classified silently" in _execute_text(), (
        "execute/SKILL.md must state that a single-line render WITH a `parent` edge "
        "present is never silently classified as either standalone or a plan"
    )


def test_ambiguous_case_reports_the_miswired_edge_and_stops():
    text = _execute_text()
    assert "Stop and report the suspected mis-wired parent edge" in text, (
        "execute/SKILL.md must report the suspected mis-wired parent edge and stop for "
        "the ambiguous case — it must never guess a classification"
    )
    assert MISWIRED_LESSON_LINK in text, (
        "execute/SKILL.md must cite "
        f"{MISWIRED_LESSON_LINK!r} when reporting the ambiguous case — the lesson that "
        "graph edges require bare task names is exactly why a mis-wired edge renders "
        "as a lone node"
    )


# --- the (runnable) dispatch gate ---


def test_runnable_marker_gates_dispatch():
    assert "carries the `(runnable)` marker" in _execute_text(), (
        "execute/SKILL.md must gate standalone `ready` dispatch on the `(runnable)` "
        "marker — a node without it has an unresolved dependency, not a green light"
    )


def test_missing_runnable_marker_reports_unmet_dependency():
    assert "unmet `depends-on` edge" in _execute_text(), (
        "execute/SKILL.md must state that a standalone node without the `(runnable)` "
        "marker has an unmet `depends-on` edge, and that this is reported rather than "
        "guessed at"
    )


# --- refine runs inline ---


def test_open_standalone_task_runs_refine_inline():
    assert "_shared/refine.md" in _execute_text(), (
        "execute/SKILL.md must reference `_shared/refine.md` directly for the inline "
        "refine run on a standalone `open` task — no skill-to-skill hop"
    )


def test_execute_names_the_refine_escalation_outcome():
    assert ESCALATION_HEADING in _execute_text(), (
        f"execute/SKILL.md must name {ESCALATION_HEADING!r} as the outcome that stops "
        "the loop rather than proceeding to dispatch — an escalated task never reaches "
        "executor dispatch"
    )


# --- simplify trigger set on a standalone task ---


def test_simplify_trigger_large_bar():
    assert "Large bar (200+ lines or 5+ files)" in _execute_text(), (
        "execute/SKILL.md must name the existing Large bar as one of the standalone "
        "simplify triggers, reusing the same boundary values as the review-size table"
    )


def test_simplify_trigger_records_dispatch_count_in_end_phases():
    assert (
        "record the running dispatch count in the task's `## End Phases` notes"
        in _execute_text()
    ), (
        "execute/SKILL.md must state that a standalone task's repeated-dispatch "
        "simplify trigger is tracked by recording the dispatch count in `## End "
        "Phases` notes as it happens, so the trigger survives a resumed run"
    )


def test_simplify_trigger_done_with_concerns_naming_structure():
    assert (
        "DONE_WITH_CONCERNS` naming duplication/scaffolding/structure" in _execute_text()
    ), (
        "execute/SKILL.md must name DONE_WITH_CONCERNS citing duplication, scaffolding, "
        "or structure as a standalone simplify trigger"
    )


def test_simplify_skip_report_phrase_is_exact():
    assert "skipped — single leaf, no trigger" in _execute_text(), (
        "execute/SKILL.md must report exactly \"skipped — single leaf, no trigger\" "
        "when no standalone simplify trigger fired — this is a discovery handle a "
        "later scan can grep for, not a paraphrasable summary"
    )


def test_end_phases_created_at_first_executor_dispatch():
    assert "created at the first executor dispatch" in _execute_text(), (
        "execute/SKILL.md must state that on a standalone task `## End Phases` is "
        "created at the first executor dispatch — widened from its normal "
        "end-pipeline-only lifecycle so the dispatch-count note has somewhere to live "
        "from the start"
    )


# --- cross-file coupling: the --interactive flag must not silently diverge ---


def test_interactive_flag_matches_between_execute_and_refine():
    assert "--interactive" in _execute_text(), (
        "execute/SKILL.md must name the `--interactive` flag for its inline refine "
        "invocation"
    )
    assert SHARED_REFINE.exists(), f"Expected _shared/refine.md at {SHARED_REFINE}"
    assert "--interactive" in SHARED_REFINE.read_text(), (
        "_shared/refine.md must name the `--interactive` flag — if either file's flag "
        "string is renamed without the other, execute's inline invocation silently "
        "stops opting into interactive mode"
    )


# --- existing guards this slice must not break ---


def test_inlined_review_threshold_values_still_present():
    text = _execute_text()
    for value in ("30", "200", "5+"):
        assert value in text, (
            f"execute/SKILL.md must retain the inlined review-threshold value {value!r} "
            "(see test_craft_skills_generic.py's _INLINED_VALUES guard)"
        )


def test_dispatched_agents_still_named():
    text = _execute_text()
    for agent in (
        "assumption-prover",
        "executor",
        "drift-gate",
        "test-runner",
        "troubleshooter",
        "simplifier",
        "security-auditor",
        "code-reviewer",
    ):
        assert agent in text, (
            f"execute/SKILL.md must retain the {agent!r} dispatch (see "
            "test_craft_skills_registrable.py's _EXECUTE_DISPATCHED_AGENTS guard)"
        )
