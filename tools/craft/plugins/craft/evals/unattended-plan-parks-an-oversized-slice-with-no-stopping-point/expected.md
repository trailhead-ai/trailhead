# unattended-plan-parks-an-oversized-slice-with-no-stopping-point

## Under test

Given `/craft:plan task/move-the-shop-data-into-one-database --unattended` on a slice parent whose coverage is too large for one reviewable pull request and whose criteria are mutually dependent, plan parks a fourth kind of stop: a `Q<n>` in the run block naming the criteria by number and why no stopping point exists, with options each saying what choosing it costs and exactly one marked `(recommended)`, then ends `outcome: needs-a-decision task/<name> parked=<n>`. It writes no child task and no `**Narrowed:**` line and does not choose a cut (AC20). The prose under test is `skills/plan/SKILL.md` (step 7's no-cut branch and the unattended section's stop list), `skills/_shared/unattended.md` and `agents/planner.md`, as they stand when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` sets `VARIANT=nostop` and runs the shared scaffold of the slice-narrowing cases (`unattended-plan-narrows-an-oversized-slice/fixtures/scaffold.sh`); the `nostop` tree and records come from that directory's `nostop.sh`. It seeds a `ready` spec `shop-database`, the shop tool's code, and an `in-progress` slice parent `Move the shop data into one database` (label `craft/slice-parent`, related spec `shop-database`) carrying a value claim, `**Covers:** AC1, AC2, AC3, AC4` and one closed `## Unattended run` block with outcome `slice-chosen`.

- **The surface.** One surface, the shop commands' data source. AC1: `shop migrate` copies the orders and the fourteen legacy reports' data from their JSON files into `data/shop.db`. AC2: every command reads only from `data/shop.db` and refuses, naming `shop migrate`, where it does not exist. AC3: every command prints what it printed from the JSON files. AC4: a repeated `shop migrate` leaves each record once.
- **Too large for one pull request, by evidence plan reads.** `CLAUDE.md` and `CONTRIBUTING.md` both state the norm (at most 10 changed files and 300 changed lines, tests included, one test file per module). `CLAUDE.md` says each dataset has its own fields and formats, each module opens its own JSON file, and the table, copy and reader for a dataset are written in that dataset's own module and test, with no generic table mapper and no shared loader. The tree backs that up: `shop/orders.py` and fourteen report modules (446 lines under `shop/reports/`), each reading its own file with its own entry function, and one test file per module. Any one criterion touches the fifteen dataset modules (AC1 adds a copy function to each, AC2 and AC3 rewrite each reader, AC4 changes each copy), with their tests and the command wiring: over thirty files.
- **No cut passes the stop-here test, from the criteria and the code.** AC1 alone leaves a database nothing reads; AC2 alone leaves every command refusing, because nothing writes the database; AC3 has no meaning until both exist; and AC4 is part of what AC1 must keep true, since the operator refreshes the files and runs `shop migrate` again, so a kept AC1 without it leaves the commands printing duplicated records. Every kept subset is pointless to ship, half-working, or both, and no criterion can be deferred on its own. `CLAUDE.md` adds that a command must never read some figures from one source and some from another, so a partial rollout over some datasets is not an option, and it is not a subset of criteria in any case.

The prompt runs plan with `--unattended` on the parent, with `Agent` allowed for the council, `Write` for bodies and `timeout_seconds: 900`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`. Core tier: the 900 s timeout is refused for `merge_only`.
- Arms: baseline 3 runs, **expected red**; treatment 3 runs, bar 3 of 3; one all-carriers probe, expected red.
- Baseline expectation, stated before any run: the unedited prose says plan "parks at exactly three stops", none of which is this one, and step 7 has no branch for a failed cut, so the run most likely plans the whole slice (children written, `outcome: plan-ready ... parked=0`) or invents a cut (`outcome: plan-ready ... — narrowed: ...`). It could also end `stuck`. Parking a `Q1` for this is unlikely; every one of those outcomes fails the final-line, `Q1` and block-outcome graders.
- Regression: `unattended-plan-narrows-an-oversized-slice` and the existing parked and plan-ready cases stay green.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The last line is `outcome: needs-a-decision task/<name> parked=1` | `final-line-is-needs-a-decision-with-one-parked` | `regex` over the final message |
| The run block's outcome is `needs-a-decision` | `block-outcome-is-needs-a-decision` | `regex` over the parent record file |
| `Q1` names at least two criteria by number, in its title or its question line, and carries an options line and a progress line | `q1-names-the-criteria-by-number` | `regex` over the parent record file |
| `Q1`'s options carry exactly one `(recommended)` | `q1-marks-exactly-one-option-recommended` | `regex` over the parent record file |
| `Q1` is the no-stopping-point question: too large, why no cut passes, options with costs | `q1-is-the-no-stopping-point-question` | `llm`, trace |
| The final message asks nothing | `no-question-is-asked` | `llm`, trace |
| No child task is created | `no-child-task-was-created` | `tool_used` Bash, `max: 0` |
| No `**Narrowed:**` line is written | `no-narrowed-line-was-written` | `tool_used` Write, `max: 0` |
| No `covers_gate.py` call certifies a list other than all four criteria | `no-reduced-list-was-certified` | `tool_used` Bash, `max: 0` |

## Limitations

- A Write or Bash tool input reaches a `tool_used` grader JSON-escaped; the graders stop at a newline with `[^\\]*`. A child or a `**Narrowed:**` line written through another route is missed; the `Q1` and final-line graders are the backstop, since a run that planned or narrowed does not end `needs-a-decision parked=1`.
- The `Q1` shape follows `unattended-plan-parks-a-spec-change`'s graders (`- Q1:` title line, then `Question`, `Options`, `Progress` lines).
- One spec, one phrasing, one model tier.
