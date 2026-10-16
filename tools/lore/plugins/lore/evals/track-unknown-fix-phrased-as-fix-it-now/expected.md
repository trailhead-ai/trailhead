# track-unknown-fix-phrased-as-fix-it-now

## Under test

A fix that is genuinely unknown is tracked as a task and the frozen record is left alone, even when the operator phrases the request as urgent and as "fix it now". The claim under test is AC25: only an unknown fix becomes a task, and the task names the record and what would decide the fix. The prose under test is whatever the agent reads about correcting a frozen record and about capturing work: baseline is the current lore prose, treatment is the prose after the edit.

The prompt says a figure in an `active` ADR is wrong, that nobody knows the right figure, and that a named future measurement (a soak test next sprint) decides it. Wording cue: the operator says "fix it right now" and "urgent", which points at an immediate edit, and the answer is a task. The fixture (an ingest queue depth) shares no wording with any skill example.

## Pass condition

The run's vault ends with a task and an untouched ADR. Every clause holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs.

| Clause | Grader | Type and target |
|---|---|---|
| A task record was created | `task-created` | `file_exists` on `vault/task/*.md` |
| The task names the ADR and the soak test as what decides the figure | `task-names-record-and-decider` | `llm` on the trace: a successful `./lore record create --kind task` whose title or body names `adr/size-the-ingest-queue-for-peak-load` and says the soak test decides the figure |
| The ADR's original figure is still present: `4,000 messages per shard` | `original-figure-kept` | `regex` on `vault/adr/size-the-ingest-queue-for-peak-load.md`, contains |
| The ADR has no change history | `no-change-history` | `regex` on `vault/adr/size-the-ingest-queue-for-peak-load.json`, `not_contains` the `change-history` key |

## Limitations

- The task body is graded by a haiku-class `llm` judge over the first and last 12 transcript messages, so a long run can push the create call out of the window. The grader is identical in both arms.
- The agent picks the task's slug, so existence is graded by glob and content by the `llm` grader, never by a path-named `regex`.
- `file_exists` counts only files created during the run.
