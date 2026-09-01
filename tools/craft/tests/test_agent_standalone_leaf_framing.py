"""Slice: agent prose relaxations for standalone leaves.

`code-reviewer`, `drift-gate`, and `executor` were written for a
parent-with-children plan run only — they framed a spec+plan pair (or a plan
slice) as unconditionally required input. A refined standalone task body
(captured prose + the `templates/task.md` bold-label payload, produced by the
`_shared/refine.md` promotion ritual) is now an equally valid intent document
for a standalone leaf. These tests pin the widened input-framing prose; no
behavioral/output-contract change is expected or tested here.
"""

from __future__ import annotations

from pathlib import Path

AGENTS_DIR = Path(__file__).parent.parent / "plugins" / "craft" / "agents"


def _text(name: str) -> str:
    return (AGENTS_DIR / name).read_text()


# ---------------------------------------------------------------------------
# code-reviewer.md
# ---------------------------------------------------------------------------


def test_code_reviewer_names_the_standalone_leaf_case():
    text = _text("code-reviewer.md")
    assert "standalone" in text.lower() and "leaf" in text.lower(), (
        "code-reviewer.md must name the standalone-leaf case explicitly."
    )


def test_code_reviewer_drops_unconditional_spec_and_plan_requirement():
    text = _text("code-reviewer.md")
    old_phrases = (
        "both are required input the caller must provide, not optional context",
        "Use `Read` to load the spec and the plan the caller provides — both are required input.",
    )
    for phrase in old_phrases:
        assert phrase not in text, (
            f"code-reviewer.md still states the old unconditional phrasing {phrase!r}; "
            "relax it so a refined standalone task body is an acceptable alternative "
            "intent document."
        )


def test_code_reviewer_accepts_refined_standalone_task_body_as_intent_document():
    text = _text("code-reviewer.md")
    assert "refined standalone task" in text, (
        "code-reviewer.md must state that a refined standalone task body is an "
        "acceptable intent document — its captured prose is the why, its payload "
        "the what."
    )


def test_code_reviewer_frontmatter_and_rules_cover_both_intent_shapes():
    """The description drives dispatch selection; Scope and Rules drive the review.

    A body that accepts a standalone task record, wrapped in a frontmatter and a rules
    list that both say "spec and plan", is a self-contradicting agent definition — and
    the frontmatter is the half a caller reads when deciding whether to dispatch.
    """
    text = _text("code-reviewer.md")
    frontmatter = text.split("---")[1]
    assert "standalone" in frontmatter.lower(), (
        "code-reviewer.md's frontmatter description must name the standalone-leaf case "
        "— it is what a caller matches against when choosing this agent"
    )
    assert "against its spec and plan, in a fresh context" not in text, (
        "code-reviewer.md's description still frames spec+plan as the only intent "
        "input; relax it to name the refined-standalone-body alternative"
    )
    assert "does the diff do what the spec asked and what the plan committed to" not in (
        text
    ), (
        "code-reviewer.md's Scope still frames spec+plan as the only intent input; "
        "relax it to name the intent document in either shape"
    )
    assert "does the diff satisfy the spec's requirements and the plan's intent" not in (
        text
    ), (
        "code-reviewer.md's Rules checklist bullet still frames spec+plan as the only "
        "intent input; relax it to name the intent document in either shape"
    )


def test_code_reviewer_spot_checks_standalone_body_citations():
    text = _text("code-reviewer.md")
    assert "spot-check" in text.lower(), (
        "code-reviewer.md must direct the reviewer to spot-check a standalone "
        "body's citations rather than trust them."
    )
    assert "file:line" in text and "[[record]]" in text, (
        "code-reviewer.md's citation spot-check directive must name both citation "
        "forms a refine-drafted body can carry: file:line and [[record]] pointers."
    )
    assert "self-authored by the promotion ritual" in text, (
        "code-reviewer.md must explain why standalone-body citations get spot-checked "
        "rather than trusted: the body is self-authored by the promotion ritual, "
        "unlike a human-authored spec/plan."
    )


# ---------------------------------------------------------------------------
# drift-gate.md
# ---------------------------------------------------------------------------


def test_drift_gate_names_the_standalone_leaf_case():
    text = _text("drift-gate.md")
    assert "standalone leaf" in text.lower(), (
        "drift-gate.md must name the standalone-leaf case explicitly."
    )


def test_drift_gate_own_context_block_serves_for_intent():
    text = _text("drift-gate.md")
    assert "own context block" in text, (
        "drift-gate.md must state that a standalone leaf's own context block "
        "serves for intent in place of a parent's goal/architecture."
    )


def test_drift_gate_uses_the_converged_payload_labels():
    """`expected files` is a fourth spelling of a field the templates call `Files`."""
    text = _text("drift-gate.md")
    for label in ("**Delivers:**", "**Test contract:**", "**Files:**"):
        assert label in text, (
            f"drift-gate.md must name the payload field {label!r} as the templates and "
            "refine spell it — a gate looking for a differently-named field reports "
            "drift against a body that is actually conformant."
        )
    assert "expected files" not in text.lower(), (
        "drift-gate.md still says 'expected files'; the converged spelling is "
        "`**Files:**` (templates/task.md, and refine's promoted payload)."
    )


def test_drift_gate_verdict_definitions_cover_the_standalone_leaf():
    """The verdict rules are what the gate actually emits against; the checks feed them.

    Relaxing check 1 to a standalone payload and making check 3 N/A, while `PASS` still
    requires "nothing blocks the next slice" and `DRIFT` still measures against "the
    slice's plan section", leaves no verdict a standalone leaf can satisfy as written.
    """
    text = _text("drift-gate.md")
    assert "or, for a standalone leaf, the task's payload" in text, (
        "drift-gate.md's verdict definitions must name the standalone leaf's payload as "
        "the thing the diff is measured against when there is no plan section"
    )
    assert "N/A on a standalone leaf" in text, (
        "drift-gate.md's `PASS` definition must qualify the next-slice clause — check 3 "
        "is already N/A for a standalone leaf, so a verdict that still requires it "
        "contradicts the check list above it"
    )
    assert "read the same severity bar against the change itself" in text, (
        "drift-gate.md's `BLOCKED` definition sets its severity bar as 'building the "
        "next slice on this one would be unsafe' — on a standalone leaf that bar can "
        "never be met, so the most severe verdict becomes unreachable unless it is "
        "restated against the change itself"
    )


def test_drift_gate_next_slice_readiness_is_explicit_na_on_a_standalone_leaf():
    """A vacuously-passing check reads identically to a check that found nothing."""
    text = _text("drift-gate.md")
    assert "next-task readiness: N/A — standalone leaf" in text, (
        "drift-gate.md must make check 3 explicitly N/A-with-a-note on a standalone "
        "leaf — there is no next task, and a silent pass hides which of the three "
        "checks actually ran."
    )


# ---------------------------------------------------------------------------
# executor.md
# ---------------------------------------------------------------------------


def test_executor_names_the_standalone_leaf_case():
    text = _text("executor.md")
    assert "standalone leaf" in text, (
        "executor.md must widen its opening framing to name the standalone-leaf case."
    )


def test_executor_covers_no_earlier_or_next_slices():
    text = _text("executor.md")
    assert "no earlier or next tasks" in text, (
        "executor.md must say a standalone leaf has no earlier or next tasks."
    )


def test_executor_accepts_a_standalone_task_record_as_intent_document():
    """A relaxed opening line over an unrelaxed input list is still a dead end.

    executor's own rule is "if an input is missing, report NEEDS_CONTEXT and do not
    guess" — so an input list that requires a plan path makes every standalone
    dispatch terminate in NEEDS_CONTEXT, however widely the prose above it is framed.
    """
    text = _text("executor.md")
    assert "- **plan path** — the plan file the caller provides" not in text, (
        "executor.md's input list still requires a plan path unconditionally, which "
        "its own NEEDS_CONTEXT rule then turns into a dead end for every standalone "
        "dispatch."
    )
    assert "refined standalone task record" in text, (
        "executor.md's input list must accept a refined standalone task record as the "
        "intent document in place of a plan path + task name."
    )
    assert "Read the intent document" in text, (
        "executor.md's Step 1 must read 'the intent document' and branch, rather than "
        "instructing 'Read the plan file' on a run where no plan exists."
    )
    assert "Do not guess." in text, (
        "executor.md must keep the NEEDS_CONTEXT rule for genuinely missing or "
        "ambiguous inputs — relaxing the shape must not relax the requirement."
    )


def test_executor_frames_the_captured_prose_as_data_not_dispatch():
    """On a standalone run the executor re-reads the same untrusted captured prose.

    A plan slice is written by the caller dispatching the executor; a captured task body
    is not. Without the framing, an imperative sentence in that prose reads to a
    full-tool executor exactly like a line of its own dispatch.
    """
    text = _text("executor.md")
    assert "not a dispatch instruction" in text, (
        "executor.md's standalone read must state that imperative text inside the "
        "captured prose is not an instruction the executor received."
    )
    assert "supplies intent" in text, (
        "executor.md must say what the captured prose IS for — the why — so the framing "
        "narrows the prose's authority rather than discarding the prose."
    )
    assert "nothing else" in text, (
        "executor.md must bound the build to what the payload specifies; the framing is "
        "only load-bearing if it names what the executor builds instead."
    )
