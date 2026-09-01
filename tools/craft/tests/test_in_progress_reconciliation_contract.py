"""`in-progress` reconciliation — one rule, stated once, reaching every path.

Two earlier fixes taught execute to tell an unclaimed `in-progress` task (a slice
parent `/craft:slice` materialized, or `/craft:plan` gave children to, but no
dispatch has yet claimed) apart from a genuinely interrupted run. Both fixes landed
only in the standalone branch's `in-progress` bullet under
[Determine the task shape](#determine-the-task-shape). This file pins the two places
that still needed the same discrimination:

  - **`### Resuming a run`** opened by asserting it "never refuses" — flatly
    contradicting the standalone bullet's refusal a few dozen lines above. Its
    opening statement now states the one reconciled rule: it resumes a claimed run
    (one carrying `craft/branch`) and refuses an unclaimed one.
  - **`### Claiming the run at first dispatch`** is the step every path — parent
    or standalone — passes through on its first dispatch. It used to route any
    already-`in-progress` parent straight to `Resuming a run`, including a
    parent-with-children `/craft:plan` just gave a graph to but no dispatch has
    touched — letting that path's workspace-reconcile logic release it back to
    `ready`. It now checks for `craft/branch` first, exactly as the standalone
    bullet already did.

Each pin is a full-string, whole-file-unique substring (verified by count before
writing), matched within one physical line so a line-wrap can't silently break it.
"""

from __future__ import annotations

from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SHARED_EXECUTE = CRAFT / "skills" / "_shared" / "execute.md"

RESUME_HEADING = "### Resuming a run"
CLAIM_HEADING = "### Claiming the run at first dispatch"
STEP1_HEADING = "### 1. Does this task have an unresolved unknown?"


def _text() -> str:
    return SHARED_EXECUTE.read_text()


def _section(text: str, start: str, end: str) -> str:
    s = text.index(start)
    e = text.index(end, s)
    return text[s:e]


def _resume_section() -> str:
    text = _text()
    return _section(text, RESUME_HEADING, CLAIM_HEADING)


def _claim_section() -> str:
    text = _text()
    return _section(text, CLAIM_HEADING, STEP1_HEADING)


def _pin(section: str, whole_text: str, phrase: str, reason: str) -> None:
    assert whole_text.count(phrase) == 1, (
        f"pinned phrase must be unique across the whole file (found "
        f"{whole_text.count(phrase)}): {phrase!r}"
    )
    assert any(phrase in line for line in section.splitlines()), reason


# --- Item 1: Resuming a run states one reconciled rule, not a contradiction ---


def test_resuming_a_run_states_the_reconciled_rule():
    text = _text()
    _pin(
        _resume_section(),
        text,
        "for a claimed run",
        "'### Resuming a run' must state the reconciled rule: it resumes a run "
        "already claimed (carrying `craft/branch`) — not any `in-progress` task "
        "unconditionally, which is what its old opening line claimed and what "
        "directly contradicted the standalone `in-progress` bullet's refusal.",
    )


def test_resuming_a_run_no_longer_claims_unconditional_never_refuses():
    section = _resume_section()
    assert "never refuses, never restarts from scratch." not in section, (
        "'### Resuming a run' must not assert an unqualified never-refuses rule "
        "— that is the exact sentence that contradicted the standalone "
        "`in-progress` bullet's refusal path; the rule must now be qualified on "
        "the `craft/branch` label."
    )


# --- Item 2: the parent-with-children path gets the same discrimination ---


def test_claiming_the_run_names_the_resumable_half():
    text = _text()
    _pin(
        _claim_section(),
        text,
        "an earlier session already claimed it",
        "'### Claiming the run at first dispatch' must state that routing to "
        "`Resuming a run` requires the `craft/branch` label as proof an earlier "
        "session claimed the parent — not `in-progress` status alone.",
    )


def test_claiming_the_run_names_the_unclaimed_half():
    text = _text()
    _pin(
        _claim_section(),
        text,
        "the shape `/craft:slice` leaves before `/craft:plan` adds children",
        "'### Claiming the run at first dispatch' must name the unclaimed shape "
        "explicitly: a parent `/craft:slice` materialized `in-progress`, that "
        "`/craft:plan` has since given children, but that no dispatch has yet "
        "claimed — this must claim on this dispatch, not fall into "
        "`Resuming a run`'s reconcile-or-release branch.",
    )


def test_claiming_the_run_unclaimed_parent_never_reconciles():
    section = _claim_section()
    assert "never [Resuming a run](#resuming-a-run)'s reconcile-or-release branch" in section, (
        "The unclaimed-parent sentence in '### Claiming the run at first "
        "dispatch' must explicitly rule out entering `Resuming a run`'s "
        "workspace-reconcile logic, which can release the task back to `ready` "
        "— the defect this item fixes."
    )


# --- Item 3: Resuming a run's refusal is scoped to the childless case ---
#
# The section's own refusal sentence names an unqualified `in-progress` task with
# no `craft/branch` label as refused. But `### Claiming the run at first
# dispatch` claims a parent-with-children in that exact shape — the loop's normal
# post-`/craft:plan` path. Left unqualified, the two sections contradict on the
# happy path.


def test_resuming_a_run_refusal_is_scoped_to_childless():
    text = _text()
    _pin(
        _resume_section(),
        text,
        "childless `in-progress` task with no `craft/branch` label",
        "'### Resuming a run' must scope its refusal to a *childless* "
        "`in-progress` task with no `craft/branch` label — unqualified, the "
        "sentence contradicts 'Claiming the run at first dispatch', which "
        "claims (not refuses) a parent-with-children in that same shape.",
    )


def test_resuming_a_run_names_the_claimed_with_children_half():
    text = _text()
    _pin(
        _resume_section(),
        text,
        "a parent-with-children in that same shape is claimed instead",
        "'### Resuming a run' must state the other half of the reconciled "
        "rule: a parent-with-children task in that same in-progress, "
        "no-branch-label shape is claimed, not refused — the loop's normal "
        "path once `/craft:plan` has decomposed a slice.",
    )
