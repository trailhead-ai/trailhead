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


def _execute_frontmatter() -> str:
    text = _execute_text()
    assert text.startswith("---\n"), "execute/SKILL.md must open with a frontmatter block"
    end = text.index("\n---", 3)
    return text[3:end]


def _section(heading: str) -> str:
    """The body of a `##` section, up to the next `##` heading."""
    text = _execute_text()
    assert heading in text, f"execute/SKILL.md must carry the {heading!r} section"
    start = text.index(heading) + len(heading)
    rest = text[start:]
    nxt = rest.find("\n## ")
    return rest if nxt < 0 else rest[:nxt]


# --- the entry contract admits both shapes ---


def test_frontmatter_does_not_deny_the_standalone_entry():
    """The body supports a standalone task; a frontmatter that denies it never gets read.

    Frontmatter is what the harness matches on to select the skill at all, so a
    DO-NOT-TRIGGER clause that reads "no plan exists yet" routes every standalone
    task away from the one skill that now knows how to run it.
    """
    frontmatter = _execute_frontmatter()
    assert "no plan exists yet" not in frontmatter, (
        "execute/SKILL.md's frontmatter still denies triggering when no plan exists — "
        "the body's standalone branch runs on a task record with no plan at all"
    )
    assert "neither a plan nor a task record" in frontmatter, (
        "execute/SKILL.md's frontmatter must keep the deny for the genuinely empty "
        "case (no plan AND no task record), rather than dropping it entirely"
    )


def test_frontmatter_names_the_standalone_task_entry():
    frontmatter = _execute_frontmatter()
    assert "standalone" in frontmatter.lower(), (
        "execute/SKILL.md's frontmatter must name the standalone task entry — it is "
        "the half a caller (and refine's own DO-NOT-TRIGGER clause, which points "
        "standalone tasks here) matches against"
    )


def test_when_to_use_admits_a_standalone_task():
    section = _section("## When to Use")
    assert "standalone task record" in section, (
        "execute/SKILL.md's When to Use must admit a standalone task record alongside "
        "an approved plan — otherwise the section contradicts the Loop's own branch"
    )


def test_skip_gate_is_an_explicit_judgment_on_a_standalone_task():
    """A single leaf always reads as "≤2 slices", so the escape would fire every time.

    The escape stays available — but as a stated call, not an accidental stall that
    silently swallows the standalone branch.
    """
    section = _section("## Skip Gate")
    assert "MAY be built inline" in section, (
        "execute/SKILL.md's Skip Gate must keep the build-it-yourself escape available "
        "for a small standalone task as an explicit MAY, not an implied default"
    )
    assert "the standalone branch below is the default" in section, (
        "execute/SKILL.md's Skip Gate must name the standalone branch as the default "
        "path — a single leaf trivially satisfies the ≤2-slices bar, so without this "
        "the gate fires on every standalone run"
    )


# --- standalone detection ---


def test_detection_requires_exactly_one_line_render():
    assert "renders exactly one line" in _execute_text(), (
        "execute/SKILL.md must state the standalone detection's first condition: "
        "`lore task graph <name>` renders exactly one line for the task"
    )


def test_shape_detection_renders_the_task_not_a_parent():
    assert "`lore task graph <task-name>`" in _execute_text(), (
        "execute/SKILL.md's shape-detection step must render the task under "
        "examination, not `<parent-name>` — the whole point of the step is that the "
        "task may have no parent at all"
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


def test_ambiguous_case_resolves_the_parent_value_before_diagnosing():
    """The ordinary cause is rooting the run at a child slice, not a broken edge.

    Reporting "suspected mis-wired edge" first sends the operator to debug a healthy
    graph. Resolving the `parent` value separates the two: it names a real task (wrong
    root) or it names nothing (mis-wired edge).
    """
    text = _execute_text()
    assert "resolve the `parent` value" in text, (
        "execute/SKILL.md's ambiguous branch must disambiguate first by resolving the "
        "`parent` value — the two causes have opposite remediations"
    )
    assert "re-root the run at that parent" in text, (
        "execute/SKILL.md must tell the operator to re-root at the resolved parent "
        "when the `parent` value names a real task — that is a live plan being entered "
        "at the wrong node, not a broken edge"
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


def test_multi_line_render_rooted_mid_tree_is_confirmed_before_walking():
    """The parent-edge check is only applied to the single-line render.

    A run rooted at a sub-plan renders many lines and passes straight into the
    parent-with-children path, silently executing a fragment of someone else's plan.
    """
    assert "you rooted the run at a sub-plan" in _execute_text(), (
        "execute/SKILL.md's parent-with-children branch must flag the mid-tree root: a "
        "multi-line render whose root itself carries a `parent` edge is a sub-plan, and "
        "the intended root gets confirmed with the operator before the walk"
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


# --- the plan-path substitution: the task record IS the intent document ---


def test_standalone_substitutes_the_task_record_for_the_plan_path():
    """Every dispatch downstream of the branch still asks for a plan path.

    Without one stated substitution rule the controller has to re-derive, at four
    separate dispatch sites, what to pass when no plan exists.
    """
    text = _execute_text()
    assert "The task record is the intent document" in text, (
        "execute/SKILL.md must state once, in the standalone branch, that the "
        "standalone task record substitutes for the plan path"
    )
    # Phase 3 is named by role rather than by agent on purpose: the per-slice region of
    # this file may not carry the `code-reviewer` token (test_review_altitude_contract).
    dispatch_sites = (
        "step 3's `executor` dispatch",
        "step 4's `drift-gate` dispatch",
        "Phase 2's `simplifier` dispatch",
        "Phase 3's whole-change correctness-review dispatch",
    )
    for site in dispatch_sites:
        assert site in text, (
            f"execute/SKILL.md's substitution rule must name {site!r} among the sites "
            "it covers — that step asks for a plan path in its own input list, and a "
            "rule that does not reach it leaves the dispatch dead-ending"
        )


# --- the standalone task's own status walk ---


def test_standalone_task_walks_its_own_status():
    text = _execute_text()
    assert "flip it `ready → in-progress` at the **first executor dispatch**" in text, (
        "execute/SKILL.md must state the standalone task's own status walk — with no "
        "parent to carry the lifecycle, the task is its own lifecycle handle"
    )
    assert "means close the task itself" in text, (
        "execute/SKILL.md must say that Phase 6's 'close the parent' means closing the "
        "standalone task itself — otherwise the run ends with the task still "
        "`in-progress` and no parent to close"
    )


def test_standalone_branch_covers_the_remaining_statuses():
    """`ready` and `open` are two of six; the rest must not fall through silently."""
    text = _execute_text()
    assert "report the blocking condition" in text, (
        "execute/SKILL.md's standalone branch must handle `blocked` — report the "
        "blocking condition and stop; execute cannot clear an external condition"
    )
    assert "Resume rather than" in text, (
        "execute/SKILL.md's standalone branch must handle `in-progress` as a resume, "
        "not a fresh dispatch"
    )
    assert "nothing to do" in text, (
        "execute/SKILL.md's standalone branch must handle the terminal statuses "
        "(`done`/`dropped`/`superseded`) — report there is nothing to do and stop"
    )


def test_standalone_resume_defines_the_zero_phase_line_case():
    """`## End Phases` exists from the first executor dispatch, holding no phase line.

    "Re-enter at the first unticked phase line" is undefined for a crash during the
    build itself: there are no phase lines at all, ticked or unticked, so the resume
    either stalls or silently skips straight into the end pipeline on an unfinished
    build.
    """
    text = _execute_text()
    assert "no ticked phase line" in text.lower(), (
        "execute/SKILL.md must define the standalone resume for an `## End Phases` "
        "section carrying no ticked phase line — the state a crash during the build "
        "leaves behind"
    )
    assert "re-enter the loop" in text.lower(), (
        "execute/SKILL.md must send a resume with no ticked phase line back into the "
        "Loop (verify the tree against the payload, re-dispatch `executor` as needed) "
        "rather than into the end pipeline — the build itself may be incomplete"
    )
    assert "the dispatch count continues" in text, (
        "execute/SKILL.md must state that a re-dispatch on resume continues the "
        "recorded dispatch count — restarting it at zero silently disarms the "
        "repeated-dispatch simplify trigger the notes exist to preserve"
    )
    assert "at the first unticked phase line" in text, (
        "execute/SKILL.md must keep the end-pipeline entry rule for the other case: "
        "once at least one phase line is ticked, the build is complete and the resume "
        "re-enters the pipeline at its first unticked phase line"
    )


# --- Phase 5 on a standalone task ---


def test_phase_5_does_not_assume_refine_wrote_the_flow_out_checklist():
    """A standalone `ready` task can arrive from anywhere, not only from refine.

    `/craft:polish` creates its briefs `--kind task --status ready` with no
    `## Flow-out` section at all, and the Red Flag forbids closing without a ticked
    checklist — so Phase 5 and Phase 6 are undefined for those tasks.
    """
    text = _execute_text()
    assert "The task carries its own `## Flow-out` checklist — refine wrote it" not in text, (
        "execute/SKILL.md still asserts unconditionally that refine wrote the "
        "standalone task's `## Flow-out` checklist"
    )
    assert "if the task body carries no `## Flow-out`" in text, (
        "execute/SKILL.md's Phase 5 adaptation must check for the checklist before "
        "working it — a standalone `ready` task promoted by something other than "
        "refine carries none"
    )
    assert (
        "append the three items from `${CLAUDE_PLUGIN_ROOT}/templates/plan.md` via "
        "`lore record update`" in text
    ), (
        "execute/SKILL.md's Phase 5 adaptation must append the missing checklist from "
        "the canonical template through the lore CLI — the same three items a planned "
        "parent carries, and never a direct vault-file edit"
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
