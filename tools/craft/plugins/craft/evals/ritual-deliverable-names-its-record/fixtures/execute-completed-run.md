You are running the `/craft:execute` ritual, exactly per the instructions appended to this
session (the wrapper skill plus the shared `_shared/execute.md` procedure it delegates to
entirely). All tasks of the plan have already been dispatched and built, the After All Tasks
pipeline has already run to completion, and every phase closed clean. Do not re-derive, re-check,
re-run, or verify any of it, and do not use any tool to look anything up. Take the state below as
already true and move straight to the Completion report.

- Parent task this run executed: `task/the-ledger-reconciliation-slice` (vault: `causewell`).
- 3 tasks, 5 dispatches (1.67/task), 28m wall clock, 1 lesson written, 1 lesson consumed.
- simplify: no changes.
- correctness: SHIP, 0 findings.
- security: skipped — no trigger.
- state-coverage: parent 3, doc 3, missing 0.
- criterion-observations: covered 2, observed 2, missing 0.
- push: 1 repo pushed.
- This is a clean close: do not automatically invoke `/portage:pull_request` — the PR decision is
  the operator's.

Now write the response you would give the operator at this point in the procedure — nothing
before it, nothing after it. Report only what your instructions say to report at this point; do
not describe, summarize, or apologize for the steps you were told to skip.
