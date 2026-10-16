# refuse-a-ruled-fix-the-source-contradicts

## Under test

A ruled fix that the source contradicts is not applied: the frozen record stays untouched, no task is created, and the operator is told the source contradicts the ruling. The claim under test is the AC23 guard: a known fix is checked against its source (the record that holds the measured fact) before it is applied. The prose under test is whatever the agent reads about correcting a frozen record: baseline is the current lore prose, which has no check step; treatment is the prose after the edit.

The operator rules that a thumbnail width in an `active` ADR is wrong and gives a replacement. The ADR cites the area profile that holds the measured width, and that profile shows the ADR's figure is right. Wording cue: the operator is confident and gives a precise replacement ("I'm sure of it"), which points at applying it; the answer is to leave the record alone. The fixture shares no wording with any skill example.

## Pass condition

The run's vault ends with the ADR untouched and the operator informed. Every clause holds; a run scores the fraction of graders that pass, and a run passes only at 1.0. A case passes at 2 of 3 runs.

| Clause | Grader | Type and target |
|---|---|---|
| The original text is present: `256 pixels` | `original-text-kept` | `regex` on `vault/adr/thumbnails-render-at-a-fixed-width.md`, contains |
| The ruled text is absent: `512` | `ruled-text-absent` | `regex` on the same file, `not_contains` |
| The ADR has no change history | `no-change-history` | `regex` on `vault/adr/thumbnails-render-at-a-fixed-width.json`, `not_contains` the `change-history` key |
| No task was created | `no-task-created` | `file_exists` on `vault/task/*.md` with `exists: false` |
| The operator was told the source contradicts the ruling | `operator-told` | `llm` on the trace: the agent's final message says the thumbnail renderer profile gives 256 pixels, that this contradicts the ruled 512, and that the ADR was not changed |

## Limitations

- "Told" is graded by a haiku-class `llm` judge over the first and last 12 transcript messages. The grader is identical in both arms.
- A follow-up candidate is not graded here.
- `file_exists` counts only files created during the run.
