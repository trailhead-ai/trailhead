"""Every craft/lore writer that lands on a frozen body, run in its exact write shape.

The freeze refuses a plain body update to a frozen record, so each writer the
inventory found must complete through a route the freeze admits: a section the
pipeline maintains (``## Slices``, ``## Driver run``), a body write that rides a
status flip off a non-frozen status, a status-only reopen, or ``amend`` with a
reason. One test per writer, each against a tmp-vault record in the status that
writer meets; a writer with no test below is a writer nothing pins.

Writer inventory (the parent's Given Axioms):

1. drive checkpoints          -> ``--diff`` append of ``## Driver run`` on a ``done`` task
2. execute close              -> one ``--status done --diff`` carrying tick + run-total row + measurement tally
3. execute INVALIDATED note   -> note rides ``--status superseded`` on the child
4. drive / slice drop remedy  -> note rides ``--status dropped`` on the slice parent
5. execute blocked note       -> on a ``done`` parent: status-only reopen, then the note
6. slice ledger append        -> full-body ``## Slices`` append on a ``ready`` spec
7. slice coverage backfill    -> full-body ``## Slices`` token edit on a ``ready`` spec
8. slice early-stop note      -> full-body ``amend`` with ``--base`` on a ``ready`` spec
9. consolidate repoint        -> ``amend --diff`` on a ``done`` task and on a ``decision``
10. lore record skill         -> a frozen record's whole-body correction goes through ``amend``

Where the writer's wording changed, the paired old write shape is run too and
must be refused: that refusal is why the shape changed.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from conftest import make_vault as _make_vault, run_cli as _run

DRIVER_RUN = "\n## Driver run\n\n- built, pushed\n"


def _create(vault, state, kind, status, body, title, extra=()):
    r = _run(["record", "create", "--kind", kind, "--title", title, "--keyword", "k",
              "--status", status, *extra], vault=vault, state_dir=state, stdin_text=body)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _md(vault: Path, rid: str) -> str:
    kind, name = rid.split("/", 1)
    return (vault / kind / f"{name}.md").read_text(encoding="utf-8")


def _sidecar(vault: Path, rid: str) -> dict:
    kind, name = rid.split("/", 1)
    return json.loads((vault / kind / f"{name}.json").read_text(encoding="utf-8"))


def _revision(vault, state, rid):
    r = _run(["record", "show", rid, "--json"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["revision"]


def _diff(old: str, new: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a", "b"))


def _update(vault, state, rid, body, *args):
    return _run(["record", "update", rid, *args], vault=vault, state_dir=state, stdin_text=body)


def _amend(vault, state, rid, body, reason, *args):
    return _run(["record", "amend", rid, "--reason", reason, "--initiated-by", "agent", *args],
                vault=vault, state_dir=state, stdin_text=body)


TASK = "# Task\n\nintro text\n"

PARENT_OPEN = (
    "# Parent\n\nintro text\n\n"
    "## End Phases\n\n- [x] Phase 5: Flow-out\n- [ ] Phase 6: Close\n\n"
    "## Run Metrics\n\n"
    "| Task | Band | Dispatches | Status |\n|---|---|---|---|\n| child | Small | 1 | DONE |\n"
)
PARENT_CLOSED = (
    PARENT_OPEN.replace("- [ ] Phase 6: Close", "- [x] Phase 6: Close")
    + "| Run total | 1 | 1 | 1.0 |\n"
)
MEASUREMENT_TALLY = "\n## Measurement tally\n\n- correctness Important: 1, local-to-one-task (task 2)\n"
CLOSE_FLAGS = ["--status", "done", "--diff", "--label", "craft/branch=feature",
               "--label", "craft/phase-boundary=abc1234"]


def test_drive_driver_run_checkpoint_diff_append_on_a_done_task_lands(tmp_path):
    """drive step 4/build checkpoints and slice close: ``--diff`` append of ``## Driver run``."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "done", TASK, "Done slice")

    r = _update(vault, state, rid, _diff(TASK, TASK + DRIVER_RUN), "--diff")

    assert r.returncode == 0, r.stderr
    assert _md(vault, rid) == TASK + DRIVER_RUN


def test_execute_close_carries_the_last_tick_and_run_total_in_the_done_write(tmp_path):
    """execute Phase 6 close: one ``--status done --diff`` write from ``in-progress``."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "in-progress", PARENT_OPEN, "Plan parent")

    r = _update(vault, state, rid, _diff(PARENT_OPEN, PARENT_CLOSED), *CLOSE_FLAGS)

    assert r.returncode == 0, r.stderr
    assert _md(vault, rid) == PARENT_CLOSED
    side = _sidecar(vault, rid)
    assert side["status"] == "done"
    assert side["labels"]["craft/phase-boundary"] == "abc1234"
    assert side["labels"]["craft/branch"] == "feature"


def test_execute_close_carries_the_measurement_tally_in_the_done_write(tmp_path):
    """The tally section rides the same ``--status done --diff`` close write."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "in-progress", PARENT_OPEN, "Plan parent")
    closed = PARENT_CLOSED + MEASUREMENT_TALLY

    r = _update(vault, state, rid, _diff(PARENT_OPEN, closed), *CLOSE_FLAGS)

    assert r.returncode == 0, r.stderr
    assert _md(vault, rid) == closed
    assert _sidecar(vault, rid)["status"] == "done"


def test_execute_measurement_tally_appended_after_the_done_close_is_refused(tmp_path):
    """Why the tally joined the close write: a later ``--diff`` append hits the freeze."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "in-progress", PARENT_OPEN, "Plan parent")
    assert _update(vault, state, rid, _diff(PARENT_OPEN, PARENT_CLOSED), *CLOSE_FLAGS).returncode == 0

    r = _update(vault, state, rid, _diff(PARENT_CLOSED, PARENT_CLOSED + MEASUREMENT_TALLY), "--diff")

    assert r.returncode != 0
    assert "[frozen-body]" in r.stderr
    assert _md(vault, rid) == PARENT_CLOSED


def test_execute_tick_written_after_the_done_close_is_refused(tmp_path):
    """The old Phase 6 shape: close, then tick as a separate ``--diff`` (why the close was folded)."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "in-progress", PARENT_OPEN, "Plan parent")
    assert _update(vault, state, rid, "", "--status", "done").returncode == 0

    r = _update(vault, state, rid, _diff(PARENT_OPEN, PARENT_CLOSED), "--diff",
                "--label", "craft/phase-boundary=abc1234")

    assert r.returncode != 0
    assert "[frozen-body]" in r.stderr
    assert _md(vault, rid) == PARENT_OPEN


def test_execute_refused_close_leaves_the_parent_in_progress_with_nothing_ticked(tmp_path):
    """A close the completion guard refuses writes neither status, tick, run total, nor label."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "in-progress", PARENT_OPEN, "Plan parent")
    parent_name = rid.split("/", 1)[1]
    _create(vault, state, "task", "ready", TASK, "Open child", ["--parent", parent_name])

    r = _update(vault, state, rid, _diff(PARENT_OPEN, PARENT_CLOSED), *CLOSE_FLAGS)

    assert r.returncode != 0
    assert _md(vault, rid) == PARENT_OPEN
    side = _sidecar(vault, rid)
    assert side["status"] == "in-progress"
    assert "craft/phase-boundary" not in side.get("labels", {})


def test_execute_invalidated_note_rides_the_supersede_flip(tmp_path):
    """execute 'Handling Assumption-Prover Status' option 3: note and ``--status superseded`` together."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "ready", TASK, "Invalidated child")
    note = TASK + "\nSuperseded: the prover invalidated the premise.\n"

    r = _update(vault, state, rid, _diff(TASK, note), "--status", "superseded", "--diff")

    assert r.returncode == 0, r.stderr
    assert _sidecar(vault, rid)["status"] == "superseded"
    assert _md(vault, rid) == note


def test_note_written_after_the_supersede_flip_is_refused(tmp_path):
    """The old shape: flip to ``superseded``, then note as a separate ``--diff``."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "ready", TASK, "Invalidated child")
    assert _update(vault, state, rid, "", "--status", "superseded").returncode == 0

    r = _update(vault, state, rid, _diff(TASK, TASK + "\nwhy\n"), "--diff")

    assert r.returncode != 0
    assert "[frozen-body]" in r.stderr
    assert _md(vault, rid) == TASK


def test_drop_remedy_note_rides_the_dropped_flip_on_a_ready_slice_parent(tmp_path):
    """drive step 5 multi-repo remedy and slice step 5 guard: ``--status dropped`` plus the why."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "ready", TASK, "Stranded slice parent")
    note = TASK + "\nDropped: the multi-repo question is resolved elsewhere.\n"

    r = _update(vault, state, rid, _diff(TASK, note), "--status", "dropped", "--diff")

    assert r.returncode == 0, r.stderr
    assert _sidecar(vault, rid)["status"] == "dropped"
    assert _md(vault, rid) == note


def test_blocked_note_on_a_done_parent_reopens_status_only_then_lands(tmp_path):
    """execute Blocked-Path Write against a ``done`` parent (drive post-build push escalation)."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "done", TASK, "Pushed parent")
    note = TASK + "\n## Blocked\n\npush rejected; needs reconciliation.\n"

    reopen = _update(vault, state, rid, "", "--status", "blocked")
    write = _update(vault, state, rid, _diff(TASK, note), "--diff")

    assert reopen.returncode == 0, reopen.stderr
    assert write.returncode == 0, write.stderr
    assert _sidecar(vault, rid)["status"] == "blocked"
    assert _md(vault, rid) == note


def test_blocked_note_retries_against_the_blocked_parent_after_a_failed_note_write(tmp_path):
    """The recovery clause: a failed note write is retried against the now-``blocked`` parent."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "done", TASK, "Pushed parent")
    note = TASK + "\n## Blocked\n\npush rejected.\n"
    assert _update(vault, state, rid, "", "--status", "blocked").returncode == 0
    stale = _diff("# Other\n\nunrelated\n", "# Other\n\nunrelated\nx\n")
    assert _update(vault, state, rid, stale, "--diff").returncode != 0

    retry = _update(vault, state, rid, _diff(TASK, note), "--diff")

    assert retry.returncode == 0, retry.stderr
    assert _md(vault, rid) == note


def test_blocked_note_with_the_flip_in_one_write_on_a_done_parent_is_refused(tmp_path):
    """The old shape: ``--status blocked --diff`` in one write against a ``done`` parent."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "done", TASK, "Pushed parent")

    r = _update(vault, state, rid, _diff(TASK, TASK + "\n## Blocked\n\nnote\n"),
                "--status", "blocked", "--diff")

    assert r.returncode != 0
    assert "[frozen-body]" in r.stderr
    assert _md(vault, rid) == TASK
    assert _sidecar(vault, rid)["status"] == "done"


SPEC = "# Spec\n\nintro text\n\n## Acceptance Criteria\n\n- AC1 first\n- AC2 second\n"
LEDGER_1 = "\n## Slices\n\n- [slice one](task/one) — covers AC1\n"


def test_slice_ledger_append_as_a_full_body_write_on_a_ready_spec_lands(tmp_path):
    """slice step 4: the ``## Slices`` append, a full-body read-modify-write."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "spec", "ready", SPEC, "Loop spec")
    grown = _md(vault, rid) + LEDGER_1

    r = _update(vault, state, rid, grown)

    assert r.returncode == 0, r.stderr
    assert _md(vault, rid) == grown


def test_slice_coverage_token_backfill_inside_slices_on_a_ready_spec_lands(tmp_path):
    """slice step 4 reconcile: the coverage-token backfill edits only a ``## Slices`` line."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "spec", "ready", SPEC + LEDGER_1, "Loop spec")
    backfilled = _md(vault, rid).replace("covers AC1", "covers AC1 (covers: AC1)")

    r = _update(vault, state, rid, backfilled)

    assert r.returncode == 0, r.stderr
    assert "(covers: AC1)" in _md(vault, rid)


def test_slice_early_stop_note_via_full_body_amend_with_base_lands_with_history(tmp_path):
    """slice step 6 early stop: ``amend --reason ... --initiated-by agent`` with ``--base``."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "spec", "ready", SPEC, "Loop spec")
    stored = _md(vault, rid)
    noted = stored + "\nLoop stopped: nothing clears the value floor.\n"

    label = _update(vault, state, rid, "", "--label", "craft/slice-loop=stopped")
    r = _amend(vault, state, rid, noted, "slice loop stopped early",
               "--base", _revision(vault, state, rid))

    assert label.returncode == 0, label.stderr
    assert r.returncode == 0, r.stderr
    assert _md(vault, rid) == noted
    history = _sidecar(vault, rid)["change-history"]
    assert [h["reason"] for h in history] == ["slice loop stopped early"]
    assert history[0]["initiated-by"] == "agent"


def test_slice_early_stop_note_as_a_plain_update_on_a_ready_spec_is_refused(tmp_path):
    """The old shape: the early-stop note as a bare ``lore record update`` full body."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "spec", "ready", SPEC, "Loop spec")

    r = _update(vault, state, rid, _md(vault, rid) + "\nLoop stopped.\n")

    assert r.returncode != 0
    assert "[frozen-body]" in r.stderr
    assert _md(vault, rid) == SPEC


REFERRER = "# Referrer\n\nSee [[lesson/old-name]] for the rule.\n"
REPOINTED = REFERRER.replace("old-name", "merged-name")


def test_consolidate_amend_diff_repoints_a_referrer_on_a_done_task(tmp_path):
    """consolidate 'rewrite or annotate' for a navigation referrer in a frozen status."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "done", REFERRER, "Finished referrer")

    r = _amend(vault, state, rid, _diff(REFERRER, REPOINTED), "fold lesson/old-name into merged-name",
               "--diff")

    assert r.returncode == 0, r.stderr
    assert _md(vault, rid) == REPOINTED
    assert len(_sidecar(vault, rid)["change-history"]) == 1


def test_consolidate_amend_diff_repoints_a_referrer_on_a_decision(tmp_path):
    """consolidate 'rewrite or annotate' for a decision referrer (frozen from creation)."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "decision", "active", REFERRER, "Decided referrer")

    r = _amend(vault, state, rid, _diff(REFERRER, REPOINTED), "fold lesson/old-name into merged-name",
               "--diff")

    assert r.returncode == 0, r.stderr
    assert _md(vault, rid) == REPOINTED


def test_consolidate_repoint_as_a_plain_update_on_a_frozen_referrer_is_refused(tmp_path):
    """The old shape: the repoint as ``lore record update`` on a ``done`` task referrer."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "done", REFERRER, "Finished referrer")

    r = _update(vault, state, rid, _diff(REFERRER, REPOINTED), "--diff")

    assert r.returncode != 0
    assert "[frozen-body]" in r.stderr
    assert _md(vault, rid) == REFERRER


def test_lore_record_skill_whole_body_correction_of_a_frozen_record_goes_through_amend(tmp_path):
    """lore record skill 'A correction replaces the whole body': full body + ``--base`` via amend."""
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "decision", "active", REFERRER, "Decided referrer")
    plain = _update(vault, state, rid, REPOINTED)

    r = _amend(vault, state, rid, REPOINTED, "correct the lesson link",
               "--base", _revision(vault, state, rid))

    assert plain.returncode != 0
    assert "[frozen-body]" in plain.stderr
    assert r.returncode == 0, r.stderr
    assert _md(vault, rid) == REPOINTED
