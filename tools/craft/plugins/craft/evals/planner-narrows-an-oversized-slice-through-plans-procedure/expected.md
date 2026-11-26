# planner-narrows-an-oversized-slice-through-plans-procedure

## Under test

Given a dispatch of the `craft:planner` agent on the slice parent of the shared narrowing fixture (intended cut: keep AC1, AC2, AC3; drop AC4, AC5), the planner defers to plan's step 7 for a slice parent judged too large: it never invents its own cut and never writes dropped criteria as partial coverage. The parent record ends with a `**Covers:**` line that omits the dropped criteria, a `**Narrowed:** dropped AC4, AC5 — <reason>` line, and no `**Partially covers:**` line naming them (AC13, AC14, AC15). The prose under test is `agents/planner.md` (the pointer to plan's step 7) and, through it, `skills/plan/SKILL.md`, as they stand when the baseline runs and after the edit.

## Fixture

The shared scaffold, `VARIANT=stop`. The prompt dispatches the agent (`Agent` is in this case's own `allowed_tools`; no runner grant is added), tells it the slice is approved and nobody is available to answer, and passes the environment facts (code under `shop/`, `./lore`, single-command Bash calls, bodies written with the file-writing tool and fed in by redirect).

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- A run scores the fraction of graders that pass and passes only at 1.0. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- Limits: `allowed_tools: [Agent, Read, Glob, Grep]`, `max_turns: 60`, `timeout_seconds: 900`.
- Arms: baseline (unedited prose) 3 runs, expected red (the planner has no narrowing step, so the coverage line stays at five criteria and no `**Narrowed:**` line exists); treatment 3 runs, bar 3 of 3; a mutation arm with only the planner's pointer removed (plan's own step 7 left in place), expected red. If the planner reads `skills/plan/SKILL.md` unprompted the mutation arm may stay green; the case-level result and the per-grader record in `tools/craft/MANUAL-EVAL.md` say so.

## Pass condition

Every clause holds. The observation point is the parent record file after the run, because the planner's write mechanism is the agent's choice.

| Clause | Grader | Type and target |
|---|---|---|
| The record's `**Covers:**` line is `AC1, AC2, AC3` | `parent-coverage-line-is-the-kept-criteria` | `regex` over the parent record file |
| A `**Narrowed:** dropped AC4, AC5 — <reason>` line is on the record | `a-narrowed-line-names-the-dropped-criteria` | `regex` over the parent record file |
| No `**Partially covers:**` line names AC4 or AC5 | `no-partially-covers-line-names-a-dropped-criterion` | `regex` over the parent record file, `not_contains` |

## Limitations

- The third clause is vacuous on a run that never writes the body; the first two require the narrowing to have been written, so such a run fails them.
- The planner's tool calls are made inside the dispatched agent and are not graded as tool uses; the cut is read from the record it left.
- A planner that dispatches no write at all (no Bash grant reaches it, or it only reports a plan) fails the first two clauses on every arm; the baseline's red is then not about narrowing. The per-grader record notes whether the record changed at all.
- One spec, one phrasing, one model tier.
