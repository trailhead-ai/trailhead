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

import re
from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
STATUS_OWNERSHIP = CRAFT / "skills" / "_shared" / "status-ownership.md"


def _text() -> str:
    return STATUS_OWNERSHIP.read_text()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _pin_normalized(phrase: str, reason: str) -> None:
    """Whitespace-insensitive pin: a rewrap that shifts a line break can't disarm it."""
    normalized = _normalize(_text())
    assert normalized.count(_normalize(phrase)) == 1, (
        f"pinned phrase must be whitespace-normalized-unique in the file "
        f"(found {normalized.count(_normalize(phrase))}): {phrase!r}"
    )
    assert reason


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


# --- the reconciliation note is scoped to the childless case ---


def test_reconciliation_scopes_refusal_to_childless():
    _pin(
        "A childless `in-progress` task with no",
        "The reconciliation note is this contract's owner and must scope its "
        "refusal to a *childless* `in-progress` task with no `craft/branch` "
        "label — unqualified, it contradicts `/craft:execute` claiming (not "
        "refusing) a parent-with-children in that same shape, the loop's "
        "normal post-`/craft:plan` path.",
    )


def test_reconciliation_names_the_claimed_with_children_half():
    _pin(
        "a parent-with-children in that same shape is claimed instead",
        "The reconciliation note must also state the other half of the rule: "
        "once `/craft:plan` gives the slice parent children, that same "
        "in-progress, no-branch-label shape is claimed by execute's first "
        "dispatch, not refused.",
    )


def test_label_conventions_name_the_unset_on_reselection_rule():
    _pin(
        "A later pass selecting again unsets it",
        "The `craft/slice-loop` registry entry must state the same "
        "last-write-wins-style rule `craft/push` already documents: a later "
        "pass that selects another slice unsets the label.",
    )


# --- "Both are single-valued" now follows three registered labels ---


def test_single_valued_summary_counts_every_registered_label():
    """The summary sentence names how many labels it covers, so it goes stale every
    time one is registered — it has already been wrong once ("Both" after a third
    landed). The count is derived from the documented entries rather than hardcoded,
    so registering the next label fails this test at the summary rather than silently
    leaving the number one short.
    """
    import re

    text = _text()
    registered = re.findall(r"^- \*\*`craft/[a-z-]+", text, re.M)
    words = {2: "Both", 3: "All three", 4: "All four", 5: "All five", 6: "All six"}
    expected = words[len(registered)]
    assert f"{expected} are **single-valued" in text or (
        expected == "Both" and "Both are **single-valued" in text
    ), (
        f"status-ownership.md documents {len(registered)} craft/ labels "
        f"({', '.join(r.split('`')[-1] for r in registered)}), so its "
        f"single-valued summary must say {expected!r} — a summary naming fewer "
        "than it covers leaves a label with no stated multiplicity"
    )
    for stale in (w for n, w in words.items() if w != expected):
        assert f"{stale} are **single-valued" not in text, (
            f"status-ownership.md's summary says {stale!r} but documents "
            f"{len(registered)} labels — stale referent"
        )


def test_single_valued_summary_states_slice_loops_multiplicity():
    _pin_normalized(
        "`craft/slice-loop` takes one of its two values",
        "status-ownership.md must state `craft/slice-loop`'s multiplicity "
        "explicitly — it is still single-valued (one of `complete` or "
        "`stopped` at a time), the same shape `craft/branch` and "
        "`craft/push` hold a single value in.",
    )


# --- the (created) -> in-progress entry names its real exit owner ---


def test_created_at_in_progress_names_plan_writes_no_status():
    _pin_normalized(
        "not `/craft:plan` — it decomposes the parent into children but writes no status",
        "status-ownership.md's `(created) → in-progress` entry must state "
        "that `/craft:plan` is not the exit owner — it writes no status at "
        "all on the slice-rooted path, so it cannot close the exit edge the "
        "section's own rule requires an exit owner to close.",
    )


def test_created_at_in_progress_names_the_real_exit_owner():
    _pin_normalized(
        "The real exit owner is the same as the `ready → in-progress` entry above",
        "status-ownership.md's `(created) → in-progress` entry must name "
        "the real exit owner: the same two execute exit writes (done and "
        "blocked) the `ready → in-progress` entry above already names, "
        "reached once execute's claim treats the now-decomposed, "
        "still-unclaimed parent as its first dispatch.",
    )


def test_slice_parent_label_is_documented_in_the_shared_contract():
    """The label is queried by two skills, so the shared contract is its owner —
    a marker documented only where it is written is one a reader of the guard
    cannot look up."""
    text = _text()
    assert "**`craft/slice-parent`**" in text, (
        "status-ownership.md must document `craft/slice-parent` alongside the "
        "other craft/ labels — it is written by /craft:slice at materialization "
        "and read by both /craft:slice's guard and /craft:plan's cross-check"
    )
    assert "written on the parent task at materialization" in text, (
        "the entry must say when it is written — at materialization, on the same "
        "create as the record"
    )
