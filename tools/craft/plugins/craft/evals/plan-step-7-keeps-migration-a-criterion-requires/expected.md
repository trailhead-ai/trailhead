# plan-step-7-keeps-migration-a-criterion-requires

Ported from the hand-run case `prototype-plan-carries-no-migration` (its `negative` fixture) to the plugin eval runner. Written before any runner run.

## Under test

The carve-out in step 7 of `skills/plan/SKILL.md`: when the renderer reports `— suppressed: migration and backfill` but an acceptance criterion requires preserving existing state, step 7 names that criterion and keeps the migration task, decomposed normally. Here AC3 requires every existing widget's `category` to survive as its first tag. This is the direction that, done wrong, silently drops a migration someone needed.

## Conditions, sample and bar

- The scaffold copies the branch's own `skills/plan/SKILL.md` into the run as `plan.md` and renders `migration-bar.txt` by piping `spec.md` through the branch's own `scripts/migration_bar.py --target-repo trailhead`, so both are the prose and renderer under test. The run is staged at step 7: the agent performs "7. Define Tasks" over the spec and presents the task list and the plan's `Given Axioms`, writing nothing.
- Runner: `claude plugin eval tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants from craft's `eval-gate.json` `runner` block.
- The case passes at 2 of 3 runs, each scoring 1.0. A scaffold failure or timeout is an error, logged apart from graded fails.
- This is a guard on the prose as it stands. The hand-run case measured the change that added these paragraphs; that change landed long ago.

## Pass condition

Every clause holds.

| Clause | Grader | Type |
|---|---|---|
| The task list carries a migration or backfill task that turns each existing widget's `category` into its initial tag | `migration-task-kept` | `llm` on the trace |
| The run names AC3 (or its requirement to preserve existing state) as why that task survived the suppression | `criterion-named` | `llm` on the trace |
| Nothing was written to disk | `nothing-written` | `tool_used` `Write`, min 0, max 0 |

## Limitations

- The hand-run case found an unaware planner also keeps this task, driven by AC3's plain wording, so this guards the carve-out's outcome rather than isolating the prose.
- Staged at step 7. One fixture, one model tier, three runs.
