# driver-closes-a-stuck-slice-instead-of-re-planning

## Under test

Step 4 of `skills/drive/SKILL.md`, the open-parent branch with no `## Driver run` block: before treating an open slice parent as resumable, the driver runs `pending_check.py stuck --spec <n> --parent <n>`; on `stuck:` it runs execute's Phase 6 inline as a no-ask close against that parent, and on a clean close continues as though no parent were open, rather than proceeding to the single-repo check and plan phase against a slice whose build is finished.

Recorded 2026-11-09, after the fact: the no-ask close this branch runs is the one `skills/_shared/execute.md` holds, so that file is also prose under test here and the case is mapped to it in `eval-gate.json`.

## Fixture

The no-ask close fixture (spec `schedule-courier-pickups`, one slice parent `in-progress` covering AC1 to AC3, one child `done`, every end phase before Phase 6 ticked, AC2 and AC3 unconfirmed manual checks, task branch pushed, repository on `main`, clean). The parent carries no `## Driver run` block. The real `pending_check.py` runs behind a logging stand-in. The prompt is `/craft:drive spec/schedule-courier-pickups`, bounded: stop once step 4 has finished (an open slice closed by it, or step 4 handing on) and report which. The bound keeps the run out of steps 5 to 11; it names neither the detector nor the close.

## Baseline-default check

The unedited step 4 sends an open parent with no block straight to step 5 and the plan phase. The default action is therefore the wrong one (resume, never detect or close), so a green baseline would be a design defect.

## Conditions, sample and bar

`claude plugin eval`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 3`, the runner block's grants. Baseline on the unedited `drive/SKILL.md` (fingerprint `09003e2305b9a38e58350689c4e7f110c824ee3f`), expected red; treatment bar 3 of 3 at 1.0. One rerun of a failed batch permitted, reported as rerun-green.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The detector ran against the parent | `detector-ran` | `regex` on `lore-calls.log` (`pending_check.py stuck --spec`) |
| The close ran: writer, gate, then the done write | `writer-verify-gate-close` | `regex` on `lore-calls.log` |
| The parent is `done` | `parent-is-done` | `regex` on the parent's `.json` |
| The driver did not go on to the single-repo check and plan against that parent | `not-resumed-into-plan` | `llm`, trace: the final message says the slice was closed and does not say it continues to step 5 or plan with that parent |
| No question about a check was asked | `no-question-asked` | `llm`, trace |

Mutation (targeted): delete only the detector clause from the no-block branch of step 4; `detector-ran` and `parent-is-done` must go red.

## Limitations

Three runs, one model tier. The prompt bound keeps the session short; the "continues as though no parent were open" tail (the slice ritual) is not exercised.
