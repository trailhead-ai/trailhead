"""Contract pins for the executor's model-escalation gate in execute.

Escalation to Opus on an `executor` dispatch is gated on evidence — a Sonnet
attempt that has already been made and observed to fail — not on a structural
proxy for slice difficulty. These pins hold the shape of that gate in
`_shared/execute.md`: the first dispatch of any unit of work runs on the
declared Sonnet tier, and the reactive paths that spend Opus after a failure
survive intact.

String-pin/structural checks, not runtime behavior, consistent with this
suite's style (see test_review_altitude_contract.py).
"""

from pathlib import Path

SKILLS_DIR = Path(__file__).parent.parent / "plugins" / "craft" / "skills"
EXECUTE_SKILL_MD = SKILLS_DIR / "_shared" / "execute.md"

TEXT = EXECUTE_SKILL_MD.read_text()


def test_no_prospective_slice_shape_escalation():
    """Slice shape — file count, cross-module reach — never buys Opus up front."""
    assert "integration-heavy" not in TEXT
    assert "3-5 files, cross-module coordination" not in TEXT


def test_first_dispatch_runs_on_the_declared_tier():
    """The dispatch step states the Sonnet-first rule explicitly."""
    assert "The first dispatch of a unit of work always runs on Sonnet" in TEXT


def test_model_selection_table_names_the_evidence_gate():
    """The Model Selection row for `executor` points at observed failure."""
    row = next(
        line for line in TEXT.splitlines() if line.startswith("| `executor` |")
    )
    assert "after an observed Sonnet failure" in row


def test_fix_passes_are_not_an_implicit_escalation():
    """A post-review fix dispatch is a first dispatch and gets no free Opus."""
    assert (
        "A fix dispatch is a first dispatch: it runs on Sonnet like any other"
        in TEXT
    )


def test_reactive_escalation_paths_survive():
    """The three evidence-backed paths that spend Opus are still there."""
    assert (
        "`troubleshooter` confirms the issue is reasoning capacity" in TEXT
    )
    assert (
        "Executor returns `DONE_WITH_CONCERNS` repeatedly on the same slice"
        " → re-dispatch with `model: \"opus\"`" in TEXT
    )
    assert "Needs more reasoning → re-dispatch with `model: \"opus\"`" in TEXT


POLISH_SKILL_MD = SKILLS_DIR / "polish" / "SKILL.md"
POLISH_TEXT = POLISH_SKILL_MD.read_text()


def test_polish_dispatch_shares_the_evidence_gate():
    """`polish` dispatches the same `executor` and gates escalation the same way."""
    assert "integration-heavy" not in POLISH_TEXT
    assert (
        "Escalate to Opus only after a Sonnet attempt on that follow-up has failed"
        in POLISH_TEXT
    )
