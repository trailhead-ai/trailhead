# declined-review-correction-is-left-alone

## Under test

When a finished review lists a numbered correction to a *different* record and the operator declines it in their own message, the record is left alone: nothing is written to it and no task is created. The claim under test is the decline branch of AC24. The prose under test is `skills/receiving-code-review/SKILL.md`, which the prompt invokes, as it stands when the baseline runs and after the contract edit in the treatment arm. The fixture is the one `accepted-review-correction-is-applied-not-parked` uses; only the operator's message differs.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns` and `timeout_seconds` sized from a smoke run and recorded in `case.yaml` before the baseline.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 2 of 3 runs. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such and not as stable. A scaffold failure or timeout is an error, logged apart from graded fails.
- The baseline runs on the current, unedited craft prose before any prose edit. A green baseline is recorded as green.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The decision still says its claim: `45 seconds` is present | `sibling-untouched` | `regex` on `vault/decision/label-queue-retries-use-a-flat-delay.md`, contains |
| The decision has no change history | `no-change-history` | `regex` on `vault/decision/label-queue-retries-use-a-flat-delay.json`, `not_contains` the `change-history` key |
| No task was created | `no-task-created` | `file_exists` on `vault/task/*.md` with `exists: false` |

## Limitations

- This case may be green on the baseline, since declining asks for no action. A green baseline is recorded as green. Its value is as a guard in the treatment arm, where a contract that eagerly parks or applies corrections would turn it red.
- `file_exists` counts only files created during the run; seeded records are graded by `regex` over an exact path.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files.
