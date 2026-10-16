# accepted-review-correction-is-applied-not-parked

## Under test

When a finished review lists a numbered correction to a *different* record and the operator accepts it in their own message, the correction is applied in the same session: the frozen record is amended in place, the amend records that the agent proposed it, and no task is created. The claim under test is the apply half of AC24. The operator phrases the acceptance as "log these so we don't lose them", which cues deferral while the answer is to act now. The prose under test is `skills/receiving-code-review/SKILL.md`, which the prompt invokes, as it stands when the baseline runs (no corrections-to-other-records contract) and after the contract edit in the treatment arm.

The fixture is a finished review (`review.md`) with no findings on the change and one numbered correction to `decision/label-queue-retries-use-a-flat-delay`, plus the `active` decision itself (retry delay of 45 seconds) and the `active` ADR that records the printer firmware's 90 second minimum (`adr/label-printer-firmware-rejects-fast-resends`). The review's drafted fix is to replace `45 seconds` with `90 seconds`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns` and `timeout_seconds` sized from a smoke run and recorded in `case.yaml` before the baseline.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 2 of 3 runs. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such and not as stable. A scaffold failure or timeout is an error, logged apart from graded fails.
- The baseline runs on the current, unedited craft prose before any prose edit. A green baseline is recorded as green.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The corrected text `90 seconds` is present | `corrected-text-present` | `regex` on `vault/decision/label-queue-retries-use-a-flat-delay.md`, contains |
| The wrong text `45 seconds` is absent | `wrong-text-gone` | `regex` on the same file, `not_contains` |
| The sidecar's change history carries an entry | `change-history-entry` | `regex` on `vault/decision/label-queue-retries-use-a-flat-delay.json`, contains the `change-history` key (seeded records have none) |
| The entry records the initiator as the agent | `initiated-by-agent` | `regex` on the same sidecar, contains `"initiated-by": "agent"` |
| No task was created | `no-task-created` | `file_exists` on `vault/task/*.md` with `exists: false` |

## Limitations

- `lore record amend --initiated-by` is stored as the `initiated-by` key of each `change-history` entry in the record's `.json` sidecar. It is recorded as stated and not verified, so the clause pins what the agent declared.
- A run that asks the operator again instead of acting writes nothing and fails the corrected-text clause. The operator has accepted, so asking again is not the expected route.
- `file_exists` counts only files created during the run; seeded records are graded by `regex` over an exact path.
- The agent's writes stay uncommitted in the vault's git, so every clause is graded on files.
