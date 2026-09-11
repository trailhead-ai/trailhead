You are running the `/craft:slice` ritual, exactly per the instructions appended to this session.
The spec argument has already been resolved and validated, the spec has already been read fresh,
the status guard has already passed (`ready`), the `## Slices` ledger has already been reconciled
and the candidate set derived, the open-slice guard has already passed (no slice is currently open
on this spec), the terminating condition does not apply (candidates remain), the next slice has
already been chosen, its value claim has already been stated to the operator, and the parent task
record has already been written and linked to the spec. The re-check for a concurrent duplicate has
already run and found no duplicate. Do not re-derive, re-check, re-run, or verify any of it, and do
not use any tool to look anything up or write anything. Take the state below as already true and
move straight to the Outcome.

- Spec this slice belongs to: `spec/dock-scheduling` (vault: `harborlight`, status `ready`).
- Chosen slice: "The berth allocation slice" — smallest-next above the value floor within the
  current phase.
- Value claim: lets a dock operator assign an inbound vessel to a specific berth and see the
  assignment reflected on the schedule board, closing the gap between "vessel is expected" and
  "vessel has a berth."
- This slice touches no visual surface beyond the schedule board update already covered by an
  earlier slice, so no `## Enumerated states` section applies.
- Spec acceptance criteria this slice makes fully green: AC3. No acceptance criteria are made only
  partially green.
- New parent task record: `task/the-berth-allocation-slice`, status `in-progress`, linked to the
  spec via `--related spec=dock-scheduling`.

Now write the response you would give the operator at this point in the procedure — nothing before
it, nothing after it. Report only what your instructions say to report at this point; do not
describe, summarize, or apologize for the steps you were told to skip.
