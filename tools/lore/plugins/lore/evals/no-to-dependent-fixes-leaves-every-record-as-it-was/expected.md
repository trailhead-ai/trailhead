# no-to-dependent-fixes-leaves-every-record-as-it-was

## Under test

When the operator answers no to the batched dependents question, the agent writes nothing to any dependent and creates nothing: no edit to the frozen restating record, none to the area, none to the linked-only task, no task for the declined fixes, no session note. The criteria are AC28 (the dependents fix is proposed, not forced) and AC29 (no record outside the operator's yes is touched), in `spec/frozen-records-are-corrected-in-place-amend-within-scope-supersede-only-on-a-material-change`, planned in `task/correcting-one-record-fixes-the-dependents-that-repeat-its-claim-and-nothing-else` (its "Dependents" step: a no leaves every dependent untouched and writes no task). The prose under test is whatever the agent reads about correcting a frozen record: in the baseline arm, the current lore and craft prose, which has no dependents step; in the treatment arm, the same plus the dependents step added to the `correct` skill.

The case uses the same seeded conversation and the same seeded vault as `yes-to-dependent-fixes-corrects-restating-records-only` (`context.history_file`, `fixtures/history.jsonl`, resumed with `--resume`, built from real `lore` output): the operator ruled the loan period for power tools is 6 days, not 4; the agent amended `decision/set-the-loan-period-for-power-tools` and asked one yes/no question listing `decision/keep-a-wipe-down-step-on-every-tool-return` (D2, frozen, text `A power tool is lent for 4 days and must be wiped down before it goes back on the shelf.`) and `area/tool-desk` (A1, text `Members borrow a power tool for 4 days; the desk opens at ten on weekdays.`). `task/check-the-drill-batteries-when-a-loan-comes-back` (T1, `done`) only links and is not listed. The prompt is the operator's next turn: "No." Wording cue: none beyond the bare answer; the answer carries the whole instruction.

## Pass condition

The run's vault ends with D2, A1 and T1 exactly as seeded and nothing new. "Dependents untouched" is the whole pass line here, and it holds because the answer was no. No clause reads the first, already corrected decision. Every clause holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs. The graders are identical in the baseline and the treatment arm.

| Clause | Grader | Type and target |
|---|---|---|
| D2 still has its original sentence `A power tool is lent for 4 days and must be wiped down before it goes back on the shelf.` | `d2-text-kept` | `regex` on `vault/decision/keep-a-wipe-down-step-on-every-tool-return.md`, contains |
| D2 has no change history | `d2-no-change-history` | `regex` on `vault/decision/keep-a-wipe-down-step-on-every-tool-return.json`, `not_contains` the `change-history` key |
| A1 still has its original sentence `Members borrow a power tool for 4 days; the desk opens at ten on weekdays.` | `a1-text-kept` | `regex` on `vault/area/tool-desk.md`, contains |
| T1 still has its sentence `Test each drill battery when a loan comes back, and put flat ones on the charger.` | `t1-text-kept` | `regex` on `vault/task/check-the-drill-batteries-when-a-loan-comes-back.md`, contains |
| T1 has no change history | `t1-no-change-history` | `regex` on `vault/task/check-the-drill-batteries-when-a-loan-comes-back.json`, `not_contains` the `change-history` key |
| No new record of any kind (no task for the declined fixes, no successor, no session candidate) | `no-new-record` | `file_exists` on `vault/*/*.md` with `exists: false` |

## Limitations

- The run's `trace.jsonl` and the `llm` trace judge see only the new turns, never the seeded ones. This case is graded on vault files only, and the agent's reply to the no is not graded.
- "Every dependent byte-unchanged" is graded by the kept sentence for each of D2, A1 and T1 plus the absence of a change history on D2 and T1. A plain update of A1 that kept its sentence and appended a note is not seen, and no clause reads A1's sidecar because a plain update is not asserted to write a change history. A byte comparison is not available to the graders.
- `file_exists` counts only files created during the run, so `no-new-record` sees a task, a successor or a session note and cannot see edits to seeded records.
- `regex` graders with `source: file` do not glob, so each names an exact seeded path.
- Only a plain "No." is exercised. A reply that is neither yes nor no, and a no given after an earlier blanket acceptance, are unmeasured.
- The seeded tool results carry the real `revision:` of each scaffolded record, so the history is regenerated whenever a record body changes.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files, never on commits.

## Hand-checked outcomes

Written before any fixture or grader file exists. Correct outcome: the agent acknowledges the no, says D2 and A1 are left as they were, and writes nothing; every seeded record is as it started and no file is created.

Wrong outcomes:

- **Wf1, applies the fixes anyway.** Amends D2 and updates A1 to `6 days`. Fails: `d2-text-kept`, `d2-no-change-history`, `a1-text-kept`.
- **Wf2, frozen only.** Amends D2 and leaves A1. Fails: `d2-text-kept`, `d2-no-change-history`.
- **Wf3, area only.** Updates A1 to `6 days` and leaves D2. Fails: `a1-text-kept`.
- **Wf4, task for the declined fixes.** Leaves D2 and A1 alone and creates a task "apply the loan period to the dependents later". Fails: `no-new-record`.
- **Wf5, session candidate.** Leaves every record and writes a session candidate noting the declined fixes. Fails: `no-new-record`.
- **Wf6, touches the link-only task.** Leaves D2 and A1 and amends T1 with a sentence about the 6 day period, replacing its own. Fails: `t1-text-kept`, `t1-no-change-history`.
- **Wf7, history without a text change.** Amends D2 with the same text and a reason, writing a change history. Fails: `d2-no-change-history`.

| Clause | Passes on the correct outcome | Fails on |
|---|---|---|
| `d2-text-kept` | yes | Wf1, Wf2 |
| `d2-no-change-history` | yes | Wf1, Wf2, Wf7 |
| `a1-text-kept` | yes | Wf1, Wf3 |
| `t1-text-kept` | yes | Wf6 |
| `t1-no-change-history` | yes | Wf6 |
| `no-new-record` | yes | Wf4, Wf5 |
