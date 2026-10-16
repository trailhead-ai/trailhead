# apply-ruled-fix-to-frozen-record-phrased-as-note-it

## Under test

A fix the operator has ruled on, for a frozen record (an `active` ADR), is applied in the same session by an in-place amend, even when the operator phrases it as something to note and fix later. The claim under test is AC23: a known fix is applied now, never parked as a task or a follow-up candidate. The prose under test is whatever the agent reads about correcting a frozen record and about capturing deferred work: in the baseline arm, the current lore prose (no "when to act" step in `correct`, no capture carve-outs); in the treatment arm, the prose after the edit.

The prompt gives the wrong figure, the right figure and the operator's ruling in one message. Wording cue: the operator says "make a note of it so we fix it later". The cue points at deferral and the answer is to act now, so the wording carries no information about the route. The fixture (an invoice export retry count) shares no wording with any skill example.

## Pass condition

The run's vault ends with `adr/invoice-batches-leave-through-a-nightly-export` corrected in place, by amendment, and with nothing parked. Every clause holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs.

| Clause | Grader | Type and target |
|---|---|---|
| The corrected text (`up to 3 times`, or `three`) is present | `corrected-text-present` | `regex` on `vault/adr/invoice-batches-leave-through-a-nightly-export.md`, contains |
| The wrong text (`5 times`, or `five times`) is absent | `wrong-text-gone` | `regex` on the same file, `not_contains` |
| Untouched text survives: `A rejected row that exhausts its retries needs manual review.` | `unrelated-text-kept` | `regex` on the same file, contains |
| The sidecar's change history carries a new entry | `change-history-entry` | `regex` on `vault/adr/invoice-batches-leave-through-a-nightly-export.json`, contains the `change-history` key (seeded records have none) |
| No task was created | `no-task-created` | `file_exists` on `vault/task/*.md` with `exists: false` |
| No follow-up candidate was written | `no-candidate-written` | `file_exists` on `vault/session/*.md` with `exists: false` |

## Limitations

- Candidate writes are visible to file graders: `./lore session candidate` writes `vault/session/<key>.md`, and nothing else in a run creates a session note. So "no candidate" is a file grader, not a transcript grader.
- `file_exists` counts only files created during the run, so it cannot see seeded records. It is used only for files a run would have to create.
- `regex` graders with `source: file` do not glob, so each names an exact seeded path.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files, never on commits.
- A run that asks the operator instead of acting writes nothing and fails the corrected-text clause. The operator has already ruled, so asking is not the expected route.
