# driver-closes-a-stuck-slice-instead-of-re-planning-with-a-build-checkpoint

## Under test

Step 4 of `skills/drive/SKILL.md`, the open-parent branch where a `## Driver run` block is present: the driver runs `pending_check.py stuck` on the parent before reading the block's `**Phase:**` and re-entering at the phase after it. On `stuck:` it closes the slice (execute's Phase 6 inline, no-ask) instead of resuming into the PR tail.

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
