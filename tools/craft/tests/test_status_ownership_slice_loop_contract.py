"""status-ownership.md owns the task-status contract — bring it in line with `/craft:slice`.

`/craft:slice` materializes a slice parent task directly at `in-progress`, with no
`ready` state of its own — a **created-at** status, not the `ready → in-progress`
transition the vocabulary table already names. And it stamps `craft/slice-loop` on
the spec record to signal loop termination. This file pins status-ownership.md's
three catch-up edits:

  - the vocabulary table names `/craft:slice` as a writer of a created-at
    `in-progress`, distinct from the `ready → in-progress` transition;
  - the reconciliation note is qualified on the `craft/branch` label, matching the
    same discrimination `_shared/execute.md` now states (an unclaimed `in-progress`
    task is refused, not resumed);
  - the label registry names `craft/slice-loop`, its two values, and the
    unset-on-reselection rule, the same shape `craft/push` already documents.

Every pinned phrase is verified whole-file-unique before being asserted.
"""

from __future__ import annotations

from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
STATUS_OWNERSHIP = CRAFT / "skills" / "_shared" / "status-ownership.md"


def _text() -> str:
    return STATUS_OWNERSHIP.read_text()


def _pin(phrase: str, reason: str) -> None:
    text = _text()
    assert text.count(phrase) == 1, (
        f"pinned phrase must be unique in the file (found {text.count(phrase)}): "
        f"{phrase!r}"
    )
    assert any(phrase in line for line in text.splitlines()), reason


# --- the vocabulary table names /craft:slice's created-at write ---


def test_vocabulary_names_slice_as_a_created_at_writer():
    _pin(
        "at slice materialization",
        "The vocabulary table must name `/craft:slice` as the writer of a "
        "created-at `in-progress`, written at slice materialization.",
    )


def test_vocabulary_distinguishes_created_at_from_the_ready_transition():
    _pin(
        "created-at",
        "The new entry must call out that this is a created-at status, not "
        "the `ready → in-progress` transition the existing table entry "
        "already covers — the table does not currently express this shape.",
    )


# --- the reconciliation note is qualified on craft/branch ---


def test_reconciliation_qualifies_resume_on_the_branch_label():
    _pin(
        "only once it carries the `craft/branch`",
        "The 'in-progress is a lease stand-in' section's reconciliation note "
        "must qualify resumption on the `craft/branch` label — an in-progress "
        "task no longer resumes unconditionally.",
    )


def test_reconciliation_names_the_unclaimed_case():
    _pin(
        "materializes a parent there directly",
        "The reconciliation note must name the unclaimed shape explicitly: "
        "`/craft:slice` materializes a parent task at `in-progress` directly, "
        "before `/craft:plan` gives it children — that task was never claimed "
        "by a dispatch and must be refused, not resumed.",
    )


# --- craft/slice-loop is registered in the label conventions ---


def test_label_conventions_register_slice_loop():
    _pin(
        "**`craft/slice-loop`**",
        "The label conventions section must register `craft/slice-loop` "
        "alongside `craft/branch` and `craft/push`.",
    )


def test_label_conventions_name_both_slice_loop_values():
    text = _text()
    assert "craft/slice-loop=complete" in text and "craft/slice-loop=stopped" in text, (
        "The `craft/slice-loop` registry entry must name both of its values: "
        "`complete` and `stopped`."
    )


def test_label_conventions_name_the_unset_on_reselection_rule():
    _pin(
        "A later pass selecting again unsets it",
        "The `craft/slice-loop` registry entry must state the same "
        "last-write-wins-style rule `craft/push` already documents: a later "
        "pass that selects another slice unsets the label.",
    )
