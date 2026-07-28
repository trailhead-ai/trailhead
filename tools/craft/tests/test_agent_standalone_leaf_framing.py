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
    assert "no earlier or next slices" in text, (
        "executor.md must say a standalone leaf has no earlier or next slices."
    )
