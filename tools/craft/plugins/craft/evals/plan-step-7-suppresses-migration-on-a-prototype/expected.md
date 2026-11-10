# plan-step-7-suppresses-migration-on-a-prototype

Ported from the hand-run case `prototype-plan-carries-no-migration` (its `positive` fixture) to the plugin eval runner. Written before any runner run.

## Under test

The migration-bar paragraphs of step 7 of `skills/plan/SKILL.md`, and `scripts/migration_bar.py`. The spec's `## Maturity` stamps the target `trailhead: prototype`, so the renderer reports `— suppressed: migration and backfill`, and AC3 says no preservation of existing state is required. Step 7 then defines no migration or backfill task, and records the decision as a `Given Axioms` entry naming the renderer's resolved target repository, level and basis. That durable record is the behaviour the hand-run case upheld in both of its rounds; an unaware planner drops the migration from AC3's wording alone but never records why.

## Conditions, sample and bar

- The scaffold copies the branch's own `skills/plan/SKILL.md` into the run as `plan.md` and renders `migration-bar.txt` by piping `spec.md` through the branch's own `scripts/migration_bar.py --target-repo trailhead`, so both are the prose and renderer under test. The run is staged at step 7: the agent performs "7. Define Tasks" over the spec and presents the task list and the plan's `Given Axioms`, writing nothing.
- Runner: `claude plugin eval tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, grants from craft's `eval-gate.json` `runner` block.
- The case passes at 2 of 3 runs, each scoring 1.0. A scaffold failure or timeout is an error, logged apart from graded fails.
- This is a guard on the prose as it stands. The hand-run case measured the change that added these paragraphs; that change landed long ago.

## Pass condition

Every clause holds.

| Clause | Grader | Type |
|---|---|---|
| The task list has no migration or backfill task (nothing that backfills or migrates existing widgets' `category` into `tags`) | `no-migration-task` | `llm` on the trace |
| The `Given Axioms` record that migration and backfill was suppressed, naming level `prototype`, basis `stamp` and target repository `trailhead` | `suppression-recorded` | `llm` on the trace |
| The task list still covers AC1 (new widgets get `tags`) and AC2 (the list view groups by tag) | `feature-still-planned` | `llm` on the trace |
| Nothing was written to disk | `nothing-written` | `tool_used` `Write`, min 0, max 0 |

## Limitations

- The hand-run case's production-stamped control fixture is not ported. Its own second round found it unsound: its AC3 waives preservation, so a planner can defensibly drop the migration there. A sound control needs a new fixture.
- Staged at step 7, so the steps before it, and the renderer's invocation through `lore record show`, are not exercised.
- One fixture, one model tier, three runs.
