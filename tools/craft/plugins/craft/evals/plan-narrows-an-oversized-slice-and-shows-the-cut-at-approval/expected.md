# plan-narrows-an-oversized-slice-and-shows-the-cut-at-approval

## Under test

Given `/craft:plan task/export-the-orders-to-a-csv-file` attended, on the slice parent of the shared narrowing fixture (see `unattended-plan-narrows-an-oversized-slice/expected.md`: one surface, AC1 to AC5, intended cut keeps AC1, AC2 and AC3 and drops AC4 and AC5, with AC2's text arguing to be dropped and AC4's arguing to be kept), plan narrows the slice and, at step 9's approval, shows the operator the dropped criteria, the reason and the reviewability claim before anything is built (AC16), offering to accept the cut, keep the whole slice or re-cut. The body carries the same narrowing as the unattended case (AC13, AC14) and a `**Reviewability:**` line naming sizes (AC12's attended half). The prompt pre-approves the approach and every council disposition and tells the run to stop at the approval step and build nothing; it does not pre-approve the cut. The prose under test is `skills/plan/SKILL.md` (step 7's Size the slice part, step 8, step 9), `skills/_shared/slice.md` and `agents/planner.md`, as they stand when the baseline runs and after the edit.

## Fixture

The shared scaffold, `VARIANT=stop` (`fixtures/scaffold.sh` of this case sets it and runs `unattended-plan-narrows-an-oversized-slice/fixtures/scaffold.sh`). Intended cut pre-registered: keep AC1, AC2, AC3; drop AC4, AC5.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- A run scores the fraction of graders that pass and passes only at 1.0. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]`, `max_turns: 120`, `timeout_seconds: 900`.
- Arms: baseline (unedited prose) 3 runs, expected red (no narrowing, no `**Narrowed:**` line, no gate call on a reduced list, a final message that presents all five criteria); treatment 3 runs, bar 3 of 3.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| A written body carries a `**Covers:**` line of exactly `AC1, AC2, AC3` | `covers-line-is-the-kept-criteria` | `tool_used` Write, `min: 1` |
| No written body carries a `**Covers:**` line naming AC4 or AC5 | `no-write-gives-a-covers-line-a-dropped-criterion` | `tool_used` Write, `max: 0` |
| A written `**Narrowed:**` line reads `dropped AC4, AC5 — <reason>` | `narrowed-line-names-the-dropped-criteria-with-a-reason` | `tool_used` Write, `min: 1` |
| No written `**Partially covers:**` line names AC4 or AC5 | `no-partially-covers-line-names-a-dropped-criterion` | `tool_used` Write, `max: 0` |
| A `covers_gate.py` call certifies `--covers "AC1, AC2, AC3"` | `covers-gate-certified-the-reduced-list` | `tool_used` Bash, `min: 1` |
| A written body carries a `**Reviewability:**` line naming a number of tasks, files, eval cases or surfaces | `parent-body-carries-a-reviewability-line-naming-a-size` | `tool_used` Write, `min: 1` |
| The final message names AC4 and AC5 as dropped, gives the reason and the reviewability claim, offers accept / keep whole / re-cut and waits, with nothing built | `final-message-shows-the-cut-and-offers-the-choice` | `llm`, `focus: trace` |
| No `/craft:execute` skill run | `no-build-skill-was-run` | `tool_used` Skill, `max: 0` |
| No file under `shop/` is written | `no-code-was-written-before-approval` | `tool_used` Write, `max: 0` |
| At least one child task is created; none is titled for, or delivers, the dropped work | `a-child-task-was-created`, `no-child-task-is-titled-for-dropped-work`, `no-child-body-delivers-dropped-work` | `tool_used` Bash and Write |
| `AskUserQuestion` is not used | `no-question-is-asked` | `tool_used`, `max: 0` |

## Limitations

- Observation points and the JSON-escaping of tool inputs are as in `unattended-plan-narrows-an-oversized-slice/expected.md`: bodies are read from file-tool writes, a newline is a literal backslash-n, and a gate call is graded by being made, not by its exit code.
- The attended run prints the `/craft:execute` handoff command as part of the approval step; that is not a run of the skill, and `no-build-skill-was-run` reads the Skill tool, not the final text.
- The `llm` judge reads the first and last 12 messages and is not shown able to fail in a headless run by anything but the clauses above; the body and gate graders are the primary evidence and the judge confirms the operator-facing statement.
- A headless session never offers `AskUserQuestion`, so `no-question-is-asked` is vacuous unless plan tries.
- One spec, one phrasing, one model tier.
