"""``lore record amend --reopen/--close`` on a spec: criterion marks and the ledger snapshot.

Every test runs the real CLI against a temp vault and reads back what landed on
disk. The concurrency test holds the vault's write lock in this process while two
real processes queue behind it.
"""

from __future__ import annotations

import threading
import time

from conftest import make_vault as _make_vault, run_cli_subprocess as _run_sub
from test_record_cli_amend import _amend, _body, _create, _revision, _sidecar, _snapshot
from test_record_cli_amend_criteria import _ac

from lore import locking


def _slice(task, covers=(), partial=()):
    fields = f"`task/{task}`, closed 2026-01-01"
    if covers:
        fields += ", covers " + ", ".join(covers)
    if partial:
        fields += ", partially covers " + ", ".join(partial)
    return f"- value claim ({fields})"


def _spec_body(*slices, numbers=(1, 2, 3)):
    bullets = "\n".join(_ac(n) for n in numbers)
    body = f"# Spec\n\nintro\n\n## Acceptance Criteria\n\n{bullets}\n"
    if slices:
        body += "\n## Slices\n\n" + "\n".join(slices) + "\n"
    return body


LEDGER = (_slice("a", covers=["AC1", "AC2"]), _slice("b", covers=["AC2"], partial=["AC3"]))


def _make(tmp_path, *slices, status="ready"):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "spec", "A Spec", status=status, body=_spec_body(*slices))
    return vault, state, rid


def _marks(vault, state, rid, *flags, body="", **kw):
    kw.setdefault("base", None)
    return _amend(vault, state, rid, body, extra=list(flags), **kw)


def _mark_refused(vault, state, rid, r, before, *needles):
    assert r.returncode != 0, r.stdout
    assert "Traceback" not in r.stderr
    for needle in needles:
        assert needle in r.stderr, r.stderr
    assert "nothing was written" in r.stderr
    assert r.stdout == ""
    assert _snapshot(vault) == before


def test_marks_only_reopen_keeps_the_body_and_snapshots_the_stored_ledger(tmp_path):
    vault, state, rid = _make(tmp_path, *LEDGER)
    body_before = _body(vault, rid)
    r = _marks(vault, state, rid, "--reopen", "AC2")
    assert r.returncode == 0, r.stderr
    assert _body(vault, rid) == body_before
    [entry] = _sidecar(vault, rid)["change-history"]
    assert entry["diff"] == ""
    assert entry["criteria"] == {"reopened": ["AC2"], "ledger": ["a", "b"]}
    assert "reopened criteria: AC2 (voids coverage from task/a, task/b)" in r.stdout
    assert "note: no stdin" not in r.stderr


def test_a_forged_ledger_line_in_the_new_body_is_not_snapshotted(tmp_path):
    vault, state, rid = _make(tmp_path, *LEDGER)
    forged = _spec_body(*LEDGER, _slice("forged", covers=["AC2"]))
    r = _amend(vault, state, rid, forged, extra=["--reopen", "AC2"])
    assert r.returncode == 0, r.stderr
    [entry] = _sidecar(vault, rid)["change-history"]
    assert entry["criteria"]["ledger"] == ["a", "b"]
    assert "task/forged" not in r.stdout
    assert "voids coverage from task/a, task/b" in r.stdout
    assert entry["diff"] != ""


def test_reopening_with_no_ledger_stores_an_empty_snapshot_and_says_nothing_to_void(tmp_path):
    vault, state, rid = _make(tmp_path)
    r = _marks(vault, state, rid, "--reopen", "AC2")
    assert r.returncode == 0, r.stderr
    [entry] = _sidecar(vault, rid)["change-history"]
    assert entry["criteria"] == {"reopened": ["AC2"], "ledger": []}
    assert "reopened criteria: AC2 (nothing to void)" in r.stdout


def test_a_body_change_plus_close_writes_one_entry_carrying_both(tmp_path):
    vault, state, rid = _make(tmp_path, *LEDGER)
    new = _spec_body(*LEDGER).replace("intro", "intro, reworded")
    r = _amend(vault, state, rid, new, extra=["--close", "AC3"])
    assert r.returncode == 0, r.stderr
    [entry] = _sidecar(vault, rid)["change-history"]
    assert "reworded" in entry["diff"]
    assert entry["criteria"] == {"closed": ["AC3"]}
    assert "reworded" in _body(vault, rid)
    assert "closed criteria: AC3" in r.stdout


def test_a_removal_and_a_reopen_share_one_entry_and_both_lines_print(tmp_path):
    vault, state, rid = _make(tmp_path, *LEDGER)
    new = _spec_body(*LEDGER, numbers=(1, 2))
    r = _amend(vault, state, rid, new, extra=["--reopen", "AC2"])
    assert r.returncode == 0, r.stderr
    [entry] = _sidecar(vault, rid)["change-history"]
    assert entry["criteria"] == {"removed": ["AC3"], "reopened": ["AC2"], "ledger": ["a", "b"]}
    assert "removed criteria: AC3" in r.stdout
    assert "reopened criteria: AC2" in r.stdout


def test_several_reopened_ids_each_print_their_own_voided_tasks(tmp_path):
    vault, state, rid = _make(tmp_path, *LEDGER)
    r = _marks(vault, state, rid, "--reopen", "AC1", "--reopen", "AC2")
    assert r.returncode == 0, r.stderr
    assert "reopened criteria: AC1 (voids coverage from task/a)" in r.stdout
    assert "reopened criteria: AC2 (voids coverage from task/a, task/b)" in r.stdout


def test_a_marks_only_close_after_a_reopen_undoes_it(tmp_path):
    vault, state, rid = _make(tmp_path, *LEDGER)
    assert _marks(vault, state, rid, "--reopen", "AC2").returncode == 0
    r = _marks(vault, state, rid, "--close", "AC2")
    assert r.returncode == 0, r.stderr
    assert "closed criteria: AC2" in r.stdout
    assert _sidecar(vault, rid)["change-history"][-1]["criteria"] == {"closed": ["AC2"]}


def test_reopen_on_a_task_record_is_refused(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "task", "A Task", status="open", body="# T\n\nbody\n")
    before = _snapshot(vault)
    r = _marks(vault, state, rid, "--reopen", "AC2")
    _mark_refused(vault, state, rid, r, before, "--reopen", "spec", "task")


def test_reopen_with_revert_to_is_refused(tmp_path):
    vault, state, rid = _make(tmp_path, *LEDGER)
    assert _amend(vault, state, rid, _spec_body(*LEDGER).replace("intro", "x")).returncode == 0
    before = _snapshot(vault)
    r = _marks(vault, state, rid, "--reopen", "AC2", "--revert-to", "1",
               base=_revision(vault, state, rid))
    _mark_refused(vault, state, rid, r, before, "--reopen", "--revert-to")


def test_an_undeclared_id_is_refused_naming_it(tmp_path):
    vault, state, rid = _make(tmp_path, *LEDGER)
    before = _snapshot(vault)
    r = _marks(vault, state, rid, "--reopen", "AC9")
    _mark_refused(vault, state, rid, r, before, "error:", "AC9", "declares")


def test_a_non_canonical_id_is_refused_naming_it(tmp_path):
    vault, state, rid = _make(tmp_path, *LEDGER)
    before = _snapshot(vault)
    r = _marks(vault, state, rid, "--reopen", "AC02")
    _mark_refused(vault, state, rid, r, before, "error:", "AC02", "AC2")


def test_the_same_id_to_reopen_and_close_is_refused(tmp_path):
    vault, state, rid = _make(tmp_path, *LEDGER)
    before = _snapshot(vault)
    r = _marks(vault, state, rid, "--reopen", "AC2", "--close", "AC2")
    _mark_refused(vault, state, rid, r, before, "error:", "AC2", "choose one")


def test_closing_an_already_closed_id_is_refused(tmp_path):
    vault, state, rid = _make(tmp_path, *LEDGER)
    assert _marks(vault, state, rid, "--close", "AC3").returncode == 0
    before = _snapshot(vault)
    r = _marks(vault, state, rid, "--close", "AC3")
    _mark_refused(vault, state, rid, r, before, "error:", "AC3", "already closed")


def test_closing_an_id_no_ledger_entry_names_is_refused(tmp_path):
    vault, state, rid = _make(tmp_path, _slice("a", covers=["AC1"]))
    before = _snapshot(vault)
    r = _marks(vault, state, rid, "--close", "AC2")
    _mark_refused(vault, state, rid, r, before, "error:", "AC2", "no slice has covered it")


def test_no_stdin_and_no_marks_is_refused_naming_the_marks_fix(tmp_path):
    vault, state, rid = _make(tmp_path, *LEDGER)
    before = _snapshot(vault)
    r = _marks(vault, state, rid)
    _mark_refused(vault, state, rid, r, before, "error:", "--reopen", "--close")


def test_an_identical_body_without_marks_is_still_refused(tmp_path):
    vault, state, rid = _make(tmp_path, *LEDGER)
    before = _snapshot(vault)
    r = _amend(vault, state, rid, _body(vault, rid))
    assert r.returncode != 0
    assert "identical" in r.stderr
    assert _snapshot(vault) == before


def test_a_stale_base_on_a_marks_only_amend_is_refused_and_the_current_one_accepted(tmp_path):
    vault, state, rid = _make(tmp_path, *LEDGER)
    before = _snapshot(vault)
    r = _marks(vault, state, rid, "--reopen", "AC2", base="0" * 64)
    assert r.returncode != 0
    assert "changed after you read it" in r.stderr
    assert _snapshot(vault) == before
    ok = _marks(vault, state, rid, "--reopen", "AC2", base=_revision(vault, state, rid))
    assert ok.returncode == 0, ok.stderr


def test_two_racing_marks_only_closes_write_one_entry_and_refuse_the_second(tmp_path):
    vault, state, rid = _make(tmp_path, *LEDGER)
    args = ["record", "amend", rid, "--reason", "close it", "--initiated-by", "operator",
            "--close", "AC3"]
    results = []

    def go():
        results.append(_run_sub(args, vault=vault, state_dir=state, stdin_text=""))

    with locking.vault_write_lock(vault):
        threads = [threading.Thread(target=go) for _ in range(2)]
        for t in threads:
            t.start()
        time.sleep(4)
    for t in threads:
        t.join()
    codes = sorted(r.returncode for r in results)
    assert codes[0] == 0 and codes[1] != 0, [(r.returncode, r.stderr) for r in results]
    [loser] = [r for r in results if r.returncode != 0]
    assert "already closed" in loser.stderr
    entries = _sidecar(vault, rid)["change-history"]
    assert len(entries) == 1
    assert entries[0]["criteria"] == {"closed": ["AC3"]}
