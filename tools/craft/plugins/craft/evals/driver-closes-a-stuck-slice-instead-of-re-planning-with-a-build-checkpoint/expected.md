# driver-closes-a-stuck-slice-instead-of-re-planning-with-a-build-checkpoint

## Under test

Step 4 of `skills/drive/SKILL.md`, the open-parent branch where a `## Driver run` block is present: the driver runs `pending_check.py stuck` on the parent before reading the block's `**Phase:**` and re-entering at the phase after it. On `stuck:` it closes the slice (execute's Phase 6 inline, no-ask) instead of resuming into the PR tail.

Recorded 2026-11-09, after the fact: the no-ask close this branch runs is the one `skills/_shared/execute.md` holds, so that file is also prose under test here and the case is mapped to it in `eval-gate.json`.

## Fixture

The stuck-arm fixture (one child `done`, end phases before Phase 6 ticked, AC2 and AC3 unconfirmed manual checks) plus a `## Driver run` block recording `**Phase:** build`, as a run that died inside the build's close would leave. The prompt and its bound are the stuck arm's.

## Baseline-default check

The unedited step 4 reads `build` and re-enters at the PR tail, never detecting or closing. The default is therefore the wrong action here; a green baseline would be a design defect.

## Conditions, sample and bar

As the stuck arm: 3 runs, `-j 3`, `--ablation none`, `claude-opus-5-5`. Baseline on the unedited prose (fingerprint `09003e2305b9a38e58350689c4e7f110c824ee3f`) expected red; treatment bar 3 of 3 at 1.0. One rerun of a failed batch permitted, reported as rerun-green.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The detector ran against the parent | `detector-ran` | `regex` on `lore-calls.log` |
| The close ran: writer, gate, then the done write | `writer-verify-gate-close` | `regex` on `lore-calls.log` |
| The parent is `done` | `parent-is-done` | `regex` on the parent's `.json` |
| The run did not re-enter the PR tail | `not-resumed-into-pr-tail` | `llm`, trace |

Mutation (targeted): delete only the detector clause from the block-present branch of step 4; `detector-ran` and `parent-is-done` must go red, while the stuck arm stays green.

## Limitations

Three runs, one model tier, one recorded phase (`build`).

## Revision (2026-11-10): the driver stops at the boundary after a stuck close

Pre-registered before the batch. The operator decided that after a stuck close that finishes, the driver reports the closed slice, says its branch is pushed and that a pull request is owed (naming the branch and `/portage:pull_request`), and stops: it selects and builds no other slice in that run. Graders changed: `not-resumed-into-pr-tail` became `stops-at-the-boundary-with-a-pr-owed` (`llm`, trace: the message says the slice was closed, names the branch `pickups-can-be-booked-and-confirmed` as pushed with a pull request owed through `/portage:pull_request`, and says it stops without choosing, planning or building another slice or resuming this one), and a new `regex` grader `no-next-slice-created` fails if `lore-calls.log` shows `record create --kind task` (a new slice parent). The prompt's bound now says a close ends the run at the boundary. The other graders are unchanged. Bar 3 of 3 at 1.0 on the shipped prose; at most one rerun. The fixture's parent carries Phases 1 to 5 ticked and Phase 6 unticked, as the detector now requires; checked, no scaffold change was needed.

## Revision (2026-11-10, second): the stop does not rest on the prompt

Pre-registered before the batch. The prompt told the agent that a close "ends the run at the slice boundary" and forbade every step after 4, so the behaviour under test (drive's own prose stopping after a stuck close) was in the prompt and the arm could not go red on any prose. The prompt now bounds the session without naming that behaviour: it allows the run to continue through the slice ritual that step 4 defers to and stops it where drive's procedure would hand to portage (the PR tail) or ask the operator something, and asks for what was done to the open parent and where the run stopped. A run that goes on to choose another slice shows it through `no-next-slice-created` (no `record create --kind task` in `lore-calls.log`) and through `stops-at-the-boundary-with-a-pr-owed`. Graders: unchanged. Bar 3 of 3 at 1.0 on the shipped prose, at most one rerun. The prompt change is the twin's only change; its baseline is not run (the first case's baseline stands for both), and its treatment runs only if the spend cap allows, else it is left to the push gate.
