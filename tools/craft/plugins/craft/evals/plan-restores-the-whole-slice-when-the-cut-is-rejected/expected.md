# plan-restores-the-whole-slice-when-the-cut-is-rejected

## Under test

The reject arm of the attended narrowing case. Given `/craft:plan task/export-the-orders-to-a-csv-file` on the shared narrowing fixture (intended cut: keep AC1, AC2, AC3; drop AC4, AC5) and the prompt's pre-given answer to the approval step, "keep the whole slice" as one large pull request, plan redoes step 8's single write: the dropped criteria are restored to the coverage line they came off (the parent's coverage line plus the narrowed line's identifiers is the pre-narrow list), the `**Narrowed:**` line is removed, the covers gate certifies the restored list, and the child tasks are redone to match (AC13, AC14, AC16). The prose under test is `skills/plan/SKILL.md` (step 9's three choices and the redo), as it stands when the baseline runs and after the edit.

## Fixture

The shared scaffold, `VARIANT=stopfolded`: the `stop` slice plus one open spec follow-up, F1 (skip a legacy report whose data file is missing; its work belongs to AC4), folded into the parent by a `**Folds:**` line. The cut therefore gives F1 back, and the restore must fold it back. The prompt pre-approves the approach and every council disposition, answers the approval in advance ("keep the whole slice") and tells the run to stop after carrying the answer out.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- A run scores the fraction of graders that pass and passes only at 1.0. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]`, `max_turns: 120`, `timeout_seconds: 900`.
- Arms: baseline (unedited prose) 3 runs; treatment 3 runs, bar 3 of 3.
- **The baseline is expected red, and the reason is not the restore.** The unedited prose never narrows, so the parent's coverage is never cut and the "restored" outcome (the full coverage line, no `**Narrowed:**` line, no partial line) holds trivially there. Those three graders are therefore vacuous on the baseline and are the state the treatment must also reach. The case is red on the baseline through the clauses that require the cut to have been made and then undone: a written `**Narrowed:**` line, a gate call on the reduced list, a gate call on the restored list, and child tasks for the dropped work. The per-grader record in `tools/craft/MANUAL-EVAL.md` states which graders discriminated.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The record's final `**Covers:**` line is `AC1, AC2, AC3, AC4, AC5` again | `covers-line-is-the-whole-slice-again` | `regex` over the parent record file |
| No `**Narrowed:**` line remains on the record | `no-narrowed-line-remains` | `regex` over the parent record file, `not_contains` |
| No `**Partially covers:**` line is on the record | `no-partially-covers-line-names-a-restored-criterion` | `regex` over the parent record file, `not_contains` |
| A `covers_gate.py` call certifies the restored list `--covers "AC1, AC2, AC3, AC4, AC5"` | `covers-gate-certified-the-restored-list` | `tool_used` Bash, `min: 1` |
| The cut was made first: a written `**Narrowed:**` line `dropped AC4, AC5 — <reason>` and a gate call on `--covers "AC1, AC2, AC3"` | `the-cut-was-written-before-it-was-rejected`, `the-cut-was-certified-before-it-was-rejected` | `tool_used` Write and Bash, `min: 1` |
| The child tasks were redone for the whole slice: a child body delivers `--format jsonl`, names a legacy report module or covers AC4 or AC5 | `children-were-redone-to-cover-the-whole-slice` | `tool_used` Write, `min: 1` |
| F1, given back at the cut, is folded into the parent again in the spec record, and the parent's `**Folds:**` line names it | `f1-is-folded-back-into-the-parent-once-the-slice-is-restored`, `the-parent-folds-line-names-f1-again` | `regex` over the spec and parent record files |
| The cut gave F1 back (a written `reopen` transition for F1) and the restore folded it again (a written `fold` transition for F1) | `f1-was-given-back-when-the-cut-was-made`, `f1-was-folded-again-by-a-written-transition` | `tool_used` Write, `min: 1` |
| The council reviewed again after the children were added on restore (the builder lens dispatched at least twice) | `the-council-reviewed-again-after-the-restore` | `tool_used` Agent, `min: 2` |
| The final message reports the whole slice kept, all five criteria, nothing built | `final-message-reports-the-whole-slice-kept` | `llm`, `focus: trace` |
| No `/craft:execute` skill run; no file under `shop/` written | `no-build-skill-was-run`, `no-code-was-written-before-approval` | `tool_used`, `max: 0` |

## Limitations

- The record-file graders read the final state, so a run that restores and then writes the narrowing again would fail them; a run that never narrowed passes them, which is why the cut-was-made clauses exist. Whether the restore redid the children is observed only as "a child body for the dropped work was written at some point", not as an order of writes.
- The gate clause is graded by the call, not its exit code.
- One spec, one phrasing, one model tier.
