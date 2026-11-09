# driver-ends-a-slice-without-asking-about-its-check

## Under test

Step 9 of `skills/drive/SKILL.md`: the driver's build phase runs execute's Phase 6 as a no-ask close, so a slice covering manual checks ends at the slice boundary as a completed slice without the operator being asked about any check. The prose under test is `skills/drive/SKILL.md` step 9 (with the close it cites in `skills/_shared/execute.md`, which already holds the no-ask mode).

## Fixture

The no-ask close fixture (spec `schedule-courier-pickups`, slice parent covering AC1 to AC3, AC1 automated, AC2 observed as a manual check with no attestation, AC3 a manual check with no observation, every end phase before Phase 6 ticked, task branch pushed to a bare `origin.git`, repository on `main`, clean), plus a `## Driver run` block on the parent recording `plan`. The prompt places the session inside the driver's step 9 (steps 1 to 8 done) and bounds it: stop once step 9 has finished and say how it ended. Nothing in the prompt says to ask or not to ask.

## Baseline-default check

The unedited step 9 says the build runs in execute's attended mode and that every escalation point asks the operator. The unedited default is therefore to ask; a run that does not ask on the unedited prose would mean the fixture does not make asking the default (a design defect to report, not to grade around).

## Conditions, sample and bar

`claude plugin eval`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 3`, the runner block's grants. Arms: baseline on the unedited `drive/SKILL.md` (content fingerprint `09003e2305b9a38e58350689c4e7f110c824ee3f`), expected red; treatment on the edited prose, bar 3 of 3 at 1.0. One rerun of a failed batch permitted, reported as rerun-green.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| No question about a check is put to the operator | `no-question-asked` | `llm`, trace |
| The writer ran, then the gate, then the close write | `writer-verify-gate-close` | `regex` on `lore-calls.log` |
| The slice parent is `done` | `parent-is-done` | `regex` on the parent's `.json` |
| The run reports the build phase finished at the boundary and no escalation | `reports-the-boundary-not-an-escalation` | `llm`, trace |

Mutation (targeted): delete only step 9's no-ask citation; `no-question-asked` and `parent-is-done` must go red.

## Limitations

Three runs, one model tier, one phrasing. The session is bounded to step 9 by the prompt, so the PR tail is not exercised.
