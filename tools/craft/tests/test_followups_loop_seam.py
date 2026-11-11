"""Seam: the slice loop's scripts chained on one spec body, against the real `lore`
CLI over a throwaway vault. An entries-only slice is certified by `covers_gate
--folds`, folded and marked done by `found_work.py entry`, closed with a
four-field ledger line that `ledger_gate.py` certifies, and then
`candidate_set.py` reads the loop's end off the same body."""

from __future__ import annotations

import subprocess
import sys

import pytest
from test_found_work_seam import (  # noqa: F401
    _SCRIPTS,
    _allow_real_publish_spawn,
    _isolate_ambient_env,
    _publish_spawn_guard,
    make_spec,
    make_task,
    run_entry_cli,
    seam,
    show,
    transition_doc,
)

FIRST_LINE = "- **First** — covers everything. (`task/first`, closed 2026-01-01, covers AC1, AC2)"

BODY = f"""# Fixture spec

## Problem

A slice loop.

## Acceptance Criteria

- **AC1.** First criterion.
- **AC2.** Second criterion.

## Slices

{FIRST_LINE}

## Follow-ups

- **F1.** First thing
  - status: open
  - what: the first thing
  - where: `src/a.py`
  - found: during review
- **F2.** Second thing
  - status: open
  - what: the second thing
  - where: `src/b.py`
  - found: during review
"""


def _gate(script, body, *args):
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / script), *args], input=body, capture_output=True, text=True
    )


def _tokens(stdout):
    return dict(line.split(": ", 1) for line in stdout.splitlines())


@pytest.mark.parametrize(
    ("folded", "open_after", "eligible"),
    [(["F1", "F2"], "none", "yes"), (["F1"], "F2", "no")],
    ids=["every-entry-built", "one-entry-left-open"],
)
def test_an_entries_only_slice_ends_the_loop_only_when_no_entry_is_left_open(seam, folded, open_after, eligible):  # noqa: F811
    spec_id = make_spec(seam, BODY)
    assert spec_id == "spec/fixture-spec"
    parent = make_task(seam, "Entries slice", "the slice\n", "in-progress")
    named = "".join(f"Resolves {spec_id}#{f}: thing\n" for f in folded)
    make_task(seam, "Build the entries", f"# T\n\n**Delivers:** {named}", "done", parent)

    fold_cert = _gate("covers_gate.py", show(seam, spec_id)["body"], "--folds", ", ".join(folded))
    assert fold_cert.returncode == 0, fold_cert.stderr

    fold = run_entry_cli(seam, transition_doc("fold", folded, parent))
    assert fold.returncode == 0, fold.stderr
    done = run_entry_cli(seam, transition_doc("done", folded, parent, reason=""))
    assert done.returncode == 0, done.stderr

    line = f"- **Entries slice** — builds the follow-ups. (`{parent}`, closed 2026-01-02)"
    appended = show(seam, spec_id)["body"].replace(FIRST_LINE, FIRST_LINE + "\n" + line)
    update = seam.lore("record", "update", spec_id, stdin=appended)
    assert update.returncode == 0, update.stderr

    body = show(seam, spec_id)["body"]
    ledger = _gate("ledger_gate.py", body)
    assert ledger.returncode == 0, ledger.stdout + ledger.stderr

    derived = _gate("candidate_set.py", body)
    assert derived.returncode == 0, derived.stderr
    t = _tokens(derived.stdout)
    assert t["candidates"] == "none"
    assert t["entries-folded"] == "none"
    assert t["entries-open"] == open_after
    assert t["complete-eligible"] == eligible
