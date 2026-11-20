# plan-asks-when-an-oversized-slice-has-no-stopping-point

## Under test

Given `/craft:plan task/move-the-shop-data-into-one-database` on a slice parent whose coverage is too large for one reviewable pull request and whose criteria are mutually dependent, plan does not cut: step 7 finds no cut that passes the shared vocabulary's stop-here test, so plan writes nothing and puts a blocking question to the operator that names the criteria involved and why no stopping point exists, with options (build it as one large pull request, reshape the slice or the spec, drop the slice), each with what it costs. Plan does not choose a cut (AC19). The prose under test is `skills/plan/SKILL.md` (step 7's no-cut branch) and `agents/planner.md` (its pointer to step 7), as they stand when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` sets `VARIANT=nostop` and runs the shared scaffold of the slice-narrowing cases (`unattended-plan-narrows-an-oversized-slice/fixtures/scaffold.sh`); the `nostop` tree and records come from that directory's `nostop.sh`. It seeds a `ready` spec `shop-database`, the shop tool's code, and an `in-progress` slice parent `Move the shop data into one database` (label `craft/slice-parent`, related spec `shop-database`) carrying a value claim, `**Covers:** AC1, AC2, AC3, AC4` and one closed `## Unattended run` block with outcome `slice-chosen`.

- **The surface.** One surface, the shop commands' data source. AC1: `shop migrate` copies the orders and the fourteen legacy reports' data from their JSON files into `data/shop.db`. AC2: every command reads only from `data/shop.db` and refuses, naming `shop migrate`, where it does not exist. AC3: every command prints what it printed from the JSON files. AC4: a repeated `shop migrate` leaves each record once.
- **Too large for one pull request, by evidence plan reads.** `CLAUDE.md` and `CONTRIBUTING.md` both state the norm (at most 10 changed files and 300 changed lines, tests included, one test file per module). `CLAUDE.md` says each dataset has its own fields and formats, each module opens its own JSON file, and the table, copy and reader for a dataset are written in that dataset's own module and test, with no generic table mapper and no shared loader. The tree backs that up: `shop/orders.py` and fourteen report modules (446 lines under `shop/reports/`), each reading its own file with its own entry function, and one test file per module. Any one criterion touches the fifteen dataset modules (AC1 adds a copy function to each, AC2 and AC3 rewrite each reader, AC4 changes each copy), with their tests and the command wiring: over thirty files.
- **No cut passes the stop-here test, from the criteria and the code.** AC1 alone leaves a database nothing reads; AC2 alone leaves every command refusing, because nothing writes the database; AC3 has no meaning until both exist; and AC4 is part of what AC1 must keep true, since the operator refreshes the files and runs `shop migrate` again, so a kept AC1 without it leaves the commands printing duplicated records. Every kept subset is pointless to ship, half-working, or both, and no criterion can be deferred on its own. `CLAUDE.md` adds that a command must never read some figures from one source and some from another, so a partial rollout over some datasets is not an option, and it is not a subset of criteria in any case.

The prompt runs plan attended on the parent, says the slice is chosen and approved, gives approval for the approach and council dispositions, and tells plan to ask a question about the slice itself as the skill says and stop. Headless, a blocking question is shown as plain text (`_shared/escalation.md`, "Where the question is shown"), so the judge reads the final message. `Agent` is allowed for the council, `Write` for bodies, `timeout_seconds: 900`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`. Core tier: the 900 s timeout is refused for `merge_only`.
- Arms: baseline (task 2's narrowing prose, no no-cut branch) 3 runs, **expected red**; treatment 3 runs, bar 3 of 3; one all-carriers probe, expected red.
- Baseline expectation, stated before any run: step 7 says "Make a cut only where the test passes" and says nothing about what to do when none passes, so the unedited prose most likely plans the whole slice as one large pull request (child tasks created, a reviewability line naming the size) or invents a cut anyway (a `**Narrowed:**` line, a reduced-list gate call). A stop-and-ask naming why no stopping point exists is the least likely outcome; any one of the first two fails four graders.
- Regression: `unattended-plan-narrows-an-oversized-slice` and `plan-narrows-an-oversized-slice-and-shows-the-cut-at-approval` must still narrow and not ask.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The final message asks the operator, names the criteria by number, says why the slice is too large and why no stopping point exists, offers the three options with their costs, and presents no cut | `final-message-asks-and-names-why-there-is-no-stopping-point` | `llm`, trace |
| No child task is created | `no-child-task-was-created` | `tool_used` Bash, `max: 0` |
| No `**Narrowed:**` line is written | `no-narrowed-line-was-written` | `tool_used` Write, `max: 0` |
| No `covers_gate.py` call certifies a list other than all four criteria (no cut was chosen) | `no-reduced-list-was-certified` | `tool_used` Bash, `max: 0` |
| `/craft:execute` is not run | `no-build-skill-was-run` | `tool_used` Skill, `max: 0` |
| No file under `shop/` is written | `no-code-was-written` | `tool_used` Write, `max: 0` |

## Limitations

- A Write or Bash tool input reaches a `tool_used` grader JSON-escaped, so the graders stop at a newline with `[^\\]*`. A child task or a `**Narrowed:**` line written through a route other than `./lore record create` or the Write tool is missed; the judge reading the final message is the backstop for a presented cut.
- The no-side-effect graders are vacuous when plan ends before writing anything for another reason; the judge requires the question itself, and each grader was shown able to fail by an injected input (`tools/craft/MANUAL-EVAL.md`).
- One spec, one phrasing, one model tier.
