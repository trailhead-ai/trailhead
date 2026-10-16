# consult-lists-a-sibling-correction-without-writing-it

## Under test

When a `/craft:consult` review of one record finds that a *different* record is wrong, and the right value is determinable from the seeded records, the council's deliverable lists that correction with its drafted fix, apart from the findings on the question, and writes nothing to the other record until the operator accepts it. The claim under test is the presenting half of AC24. The prose under test is whatever the run loads for a consult: `skills/consult/SKILL.md` and `_shared/council.md`, as they stand when the baseline runs (no corrections-to-other-records contract), and the same files after the contract edit in the treatment arm.

The fixture is a draft spec (`spec/buoy-gauge-outage-tolerance`) that states a 5-minute sampling interval, a 6-hour outage tolerance and 8 KB of reserved flash at 64 bytes a reading, and an `active` decision (`decision/gauge-buffer-is-sized-for-the-sampling-interval`) that sizes the buffer from a 10-minute interval to 36 readings. The spec's own figures give 72 readings. The prompt asks a question about the spec only. It does not name the decision as the thing to check.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, one case per batch, `-j 1`.
- Limits: `allowed_tools: [Skill, Agent, Read, Glob, Grep]`, `max_turns: 30`, `timeout_seconds: 420`.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 2 of 3 runs. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such and not as stable. A scaffold failure or timeout is an error, logged apart from graded fails.
- The baseline runs on the current, unedited craft prose before any prose edit. A green baseline is recorded as green.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The decision still says its wrong claim: `ten minutes` is present | `sibling-untouched` | `regex` on `vault/decision/gauge-buffer-is-sized-for-the-sampling-interval.md`, contains |
| The decision has no change history | `no-change-history` | `regex` on `vault/decision/gauge-buffer-is-sized-for-the-sampling-interval.json`, `not_contains` the `change-history` key |
| No task was created | `no-task-created` | `file_exists` on `vault/task/*.md` with `exists: false` |
| The deliverable lists the decision's correction under a heading of its own, apart from the findings about the question, and drafts the fix (72 readings from a 5-minute interval) | `correction-listed-apart-with-fix` | `llm`, focus `trace` |

## Limitations

- The runner deletes its trace after each run and `--json` holds none, so the deliverable clause is an `llm` grader over the session trace (first and last 12 messages), which includes the consult's final message. It is a judgement, weaker than a file grader.
- `file_exists` counts only files created during the run, so it cannot see seeded records. Seeded records are graded by `regex` over an exact path.
- The runner's reported turn count is unreliable with asynchronous subagents, so limits are not sized from it.
- A run that never reaches the deliverable (a refusal at the calibration step) fails the `llm` clause and is recorded as a graded fail if it ran, or an error if the runner reports one.
