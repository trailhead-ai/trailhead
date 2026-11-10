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

## Revision (2026-11-10): the driver stops at the boundary after a stuck close

Pre-registered before the batch. The operator decided that after a stuck close that finishes, the driver reports the closed slice, says its branch is pushed and that a pull request is owed (naming the branch and `/portage:pull_request`), and stops: it selects and builds no other slice in that run. Graders changed: `not-resumed-into-plan` became `stops-at-the-boundary-with-a-pr-owed` (`llm`, trace: the message says the slice was closed, names the branch `pickups-can-be-booked-and-confirmed` as pushed with a pull request owed through `/portage:pull_request`, and says it stops without choosing, planning or building another slice or resuming this one), and a new `regex` grader `no-next-slice-created` fails if `lore-calls.log` shows `record create --kind task` (a new slice parent). The prompt's bound now says a close ends the run at the boundary. The other graders are unchanged. Bar 3 of 3 at 1.0 on the shipped prose; at most one rerun. The fixture's parent carries Phases 1 to 5 ticked and Phase 6 unticked, as the detector now requires; checked, no scaffold change was needed.

## Deviations from pre-registration (2026-11-10)

The first batch on the shipped prose scored 2 of 3 at 1.0 (one run failed `stops-at-the-boundary-with-a-pr-owed`); the one permitted rerun scored 3 of 3 at 1.0 and is reported as rerun-green. No grader was changed after a run.

## Revision (2026-11-10, second): the stop does not rest on the prompt

Pre-registered before the batch. The prompt told the agent that a close "ends the run at the slice boundary" and forbade every step after 4, so the behaviour under test (drive's own prose stopping after a stuck close) was in the prompt and the arm could not go red on any prose. The prompt now bounds the session without naming that behaviour: it allows the run to continue through the slice ritual that step 4 defers to and stops it where drive's procedure would hand to portage (the PR tail) or ask the operator something, and asks for what was done to the open parent and where the run stopped. A run that goes on to choose another slice shows it through `no-next-slice-created` (no `record create --kind task` in `lore-calls.log`) and through `stops-at-the-boundary-with-a-pr-owed`. Graders: unchanged. Bar 3 of 3 at 1.0 on the shipped prose, at most one rerun. One baseline is run, on `driver-closes-a-stuck-slice-instead-of-re-planning` only, on the pre-fix `skills/drive/SKILL.md` (content fingerprint `2b780f04b88ec81b3ed92e63df94ee0ec38e4f75`, rebuilt into a scratch copy), expected red: that prose resumes or goes on to the slice ritual rather than stopping with a pull request owed. A green baseline is reported as it comes, and the arm is not reshaped to force red. Treatment runs after it on the shipped prose.

## Results of the second revision (2026-11-10)

Baseline on the pre-fix `drive/SKILL.md` (`2b780f04b88ec81b3ed92e63df94ee0ec38e4f75`): red, 0.83 in all three runs, `stops-at-the-boundary-with-a-pr-owed` failing and every other grader passing. Treatment on the shipped prose (`8e39bdbf396b4a6eddff45f154cf3101e2e096e0`): 3 of 3 at 1.0. No grader changed after a run.
