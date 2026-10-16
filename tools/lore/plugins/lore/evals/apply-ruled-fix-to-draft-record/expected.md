# apply-ruled-fix-to-draft-record

## Under test

A fix the operator has ruled on, for a record that is not frozen (a `draft` spec), is applied in the same session by a plain update, not an amend and not a task. The claim under test is AC23's non-frozen path. The prose under test is whatever the agent reads about correcting records and about capturing deferred work: baseline is the current lore prose, treatment is the prose after the edit.

The prompt names the criterion, the wrong figure, the right figure and that the operator has ruled. Wording cue: the operator asks for it to be put on a list for the next time the spec is worked on. The cue points at deferral and the answer is to act now. The fixture (overdue invoice reminders) shares no wording with any skill example.

## Pass condition

The run's vault ends with `spec/overdue-invoice-reminders` corrected by a plain update, with no task created. Every clause holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs.

| Clause | Grader | Type and target |
|---|---|---|
| The corrected text (`7 days past due`) is present | `corrected-text-present` | `regex` on `vault/spec/overdue-invoice-reminders.md`, contains |
| The wrong text (`14 days`) is absent | `wrong-text-gone` | `regex` on the same file, `not_contains` |
| Untouched text survives: `never sent for an invoice that has been paid` | `unrelated-text-kept` | `regex` on the same file, contains |
| The sidecar has no change history (a plain update, not an amend) | `no-change-history` | `regex` on `vault/spec/overdue-invoice-reminders.json`, `not_contains` the `change-history` key |
| No task was created | `no-task-created` | `file_exists` on `vault/task/*.md` with `exists: false` |

## Limitations

- A follow-up candidate is not graded here. The task's claim for this path is "no task"; the frozen-record case grades the candidate clause.
- `regex` graders with `source: file` do not glob, so each names an exact seeded path.
- `file_exists` counts only files created during the run.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files.
