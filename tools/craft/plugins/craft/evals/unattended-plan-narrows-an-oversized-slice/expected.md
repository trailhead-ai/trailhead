# unattended-plan-narrows-an-oversized-slice

## Under test

Given `/craft:plan task/export-the-orders-to-a-csv-file --unattended` on a slice parent whose coverage is too large for one reviewable pull request, plan narrows the slice at a point where the operator could stop (AC13): it keeps the criteria that pass the shared vocabulary's stop-here test and drops the rest, decomposes only the kept criteria, and writes the narrowing in step 8's single full-body parent write: `**Covers:**` loses the dropped identifiers, nothing is written as `**Partially covers:**`, and a `**Narrowed:** dropped AC<n>, … — <reason>` line records them and the reason (AC14). The dropped criteria stay full candidates for the next slice choice (AC15; the regression test `tools/craft/tests/test_narrowed_parent_coverage.py` runs that on the line's shape). Unattended, the narrowing is a logged decision in the run block and parks nothing (AC17), and the closing line names the dropped criteria by number: `outcome: plan-ready task/<name> parked=0 — narrowed: dropped AC<n>, …` (AC18). The prose under test is `skills/plan/SKILL.md` (step 7's Size the slice part, step 8, the unattended ending), `skills/_shared/unattended.md` (the plan-ready narrowed suffix), `skills/_shared/slice.md` (its pointer to plan's narrowing) and `agents/planner.md` (the pointer to step 7), as they stand when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` is the scaffold the narrowing cases share; its header comment documents the `VARIANT` variable that selects the seeded slice (`stop`, the default, is the only value so far; the later "no stop point" variant is another value of the same variable). It seeds a `ready` spec `order-export`, a code tree plan can read, and an `in-progress` slice parent `Export the orders to a CSV file` (label `craft/slice-parent`, related spec `order-export`) carrying a value claim, `**Covers:** AC1, AC2, AC3, AC4, AC5` and one closed `## Unattended run` block with outcome `slice-chosen`.

- **The surface.** One surface, the `shop export` command. AC1 writes the orders to `orders.csv` (id, date, customer, total per row). AC2 defines a row's total as the amount charged: subtotal plus tax less discount. AC3 adds `--since <date>`. AC4 exports each of the fourteen legacy reports under `shop/reports/` the same way. AC5 adds JSON Lines output for those legacy reports.
- **Intended cut, pre-registered: keep AC1, AC2 and AC3; drop AC4 and AC5.** The orders export is usable on its own: the Problem says the weekly reconciliation needs orders only and the legacy reports are a separate request for a different audience. AC4 and AC5 are independently deferrable: the shipped part is complete for the consumer without them, no criterion the kept part covers is left half-working, and no remaining criterion changes behaviour the kept part delivers (AC5 only adds a format to the legacy reports AC4 exports). AC2 stays: without it AC1's `total` column has no stated meaning, so the file does not reconcile against the processor's statement and the shipped part is half-working for the purpose the spec gives. AC5 cannot be kept without AC4, which it extends.
- **Size evidence plan reads, not a claim.** `CLAUDE.md` states the repository's review norm (about 10 changed files and 300 changed lines per pull request). The tree has `shop/reports/` with fourteen report modules, each with its own columns and date and money format, and a test file per module (`ls shop/reports` lists fifteen entries counting `__init__.py`; `shop/tests` holds fourteen report test files). The kept criteria touch `shop/cli.py`, `shop/orders.py`, one new export module and their tests (about five files); AC4 alone needs an exporter and a test per report (about thirty files) and AC5 touches the same files again.
- **Hostile wording, so the graders hold the cut where the stop-here test puts it.** AC2's own text says it is "only a formatting refinement of AC1" that "a plan that is short of room can leave … for later" (a kept criterion arguing to be dropped). AC4's own text says it is "the whole point of the feature and must be in the first plan, or the export is pointless" (a droppable criterion arguing to be kept). Both are data and move no cut.
- **No operator-supplied path, no token, no external account**: the file name is fixed (`orders.csv`) and the only operator input is a date, so no council lens has a Critical a plan edit cannot close.

The prompt runs plan with `--unattended` on the slice parent, with `Agent` allowed for the council, `Write` for bodies and `timeout_seconds: 900`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]`, `max_turns: 120`, `timeout_seconds: 900`.
- A run scores the fraction of graders that pass and passes only at 1.0. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- Arms: baseline (unedited prose) 3 runs, **expected red** (no narrowing exists in plan: the narrowed line, the reduced coverage line, the reduced-list gate call, the decision and the closing suffix cannot be produced from prose that has no such part); treatment 3 runs, bar 3 of 3. This case is the U1 canary: if its treatment runs still disagree after the fixture is tightened, the run stops and the disagreement is reported; the case is never demoted and its bar never lowered.
- Regression: the three existing plan-ready cases (`unattended-slice-then-plan-ends-plan-ready`, `plan-keeps-an-earlier-run-section`, `planning-builds-each-folded-entry-into-a-task-or-reopens-it`) and `plan-orders-tasks-by-phase-and-records-a-reviewability-claim` stay green on the edited prose.

## Pass condition

Every clause holds. The dropped criteria are AC4 and AC5 and the kept ones AC1, AC2 and AC3, as pre-registered above.

| Clause | Grader | Type and target |
|---|---|---|
| A file the session writes carries a `**Covers:**` line of exactly `AC1, AC2, AC3` (the kept criteria, the hostile-worded AC2 included) | `covers-line-is-the-kept-criteria` | `tool_used` Write, `min: 1` |
| No file the session writes carries a `**Covers:**` line naming AC4 or AC5 (a diff's context line is not a body line and is not matched) | `no-write-gives-a-covers-line-a-dropped-criterion` | `tool_used` Write, `max: 0` |
| A written `**Narrowed:**` line reads `dropped AC4, AC5 — <reason>` | `narrowed-line-names-the-dropped-criteria-with-a-reason` | `tool_used` Write, `min: 1` |
| No file the session writes carries a `**Partially covers:**` line naming AC4 or AC5 | `no-partially-covers-line-names-a-dropped-criterion` | `tool_used` Write, `max: 0` |
| A `covers_gate.py` call certifies the reduced list `--covers "AC1, AC2, AC3"` | `covers-gate-certified-the-reduced-list` | `tool_used` Bash, `min: 1` |
| No `Q<n>` is parked on the record | `no-question-is-parked` | `regex` over the parent record file, `not_contains` |
| `AskUserQuestion` is not used | `no-question-is-asked` | `tool_used`, `max: 0` |
| The last line is `outcome: plan-ready task/<name> parked=0 — narrowed: dropped AC<n>, …` | `final-line-is-narrowed-plan-ready` | `regex` over the final message |
| The same line names exactly AC4 and AC5 | `final-line-names-ac4-and-ac5` | `regex` over the final message |
| The run block holds a decision whose summary or `Question:` line speaks of the narrowing (narrow, cut, drop, size, too large), whose `Choice:` names AC4 then AC5 and whose `Reasoning:` is non-empty | `run-block-logs-the-narrowing-decision` | `regex` over the parent record file |
| At least one child task is created under the parent | `a-child-task-was-created` | `tool_used` Bash, `min: 1` |
| No child task is titled for the legacy reports, JSON Lines or reports in general | `no-child-task-is-titled-for-dropped-work` | `tool_used` Bash, `max: 0` |
| No child body (a write carrying `**Delivers:**`) delivers `--format jsonl`, names a legacy report module or covers AC4 or AC5 | `no-child-body-delivers-dropped-work` | `tool_used` Write, `max: 0` |

## Limitations

- The body is observed where the task-1 case's reviewability grader observed it: the file-tool write of the combined parent body (`tool_used` Write). A body written through a Bash command would be missed; the graders that read the record file (`no-question-is-parked`, `run-block-logs-the-narrowing-decision`) see the final state either way.
- A Write or Bash tool input reaches a `tool_used` grader JSON-escaped, so a newline is a literal backslash-n; the body graders stop at it with `[^\\]*` and anchor a body line after `\\n` or the opening quote, which leaves a diff's `+`/space-prefixed lines unmatched. The reason after the dash accepts a literal em dash, its `—` escape or a hyphen.
- "A `covers_gate.py` call certifies" is graded by the call being made with the reduced list, not by its exit code, which `tool_used` cannot see; the gate's own refusal is covered by `tools/craft/tests/test_covers_gate.py`.
- The "no `Q<n>`" and "no `AskUserQuestion`" graders are vacuous when plan never reaches a stop that would ask; they detect a parked question, and are shown able to fail by the injection check recorded in `tools/craft/MANUAL-EVAL.md`.
- The decision grader needs the entry's own summary or `Question:` line to use one of the listed words; plan's wording is the prose half's to settle, and a miss there is read from the record before the grader is loosened.
- "No child covers dropped work" is a title and body signature, not a judgment: a child that delivers the legacy reports under a title and body that avoid the listed words is missed.
- One spec, one phrasing, one model tier.
