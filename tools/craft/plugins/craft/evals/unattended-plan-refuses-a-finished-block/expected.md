# unattended-plan-refuses-a-finished-block

## Under test

`/craft:plan task/<slice> --unattended` on a slice whose latest block already ended `plan-ready` (plan, children and council complete). The run is refused: `outcome: refused task/<name> parked=0 — <why>; <what to run instead>`, with a reason that names what is finished, and nothing is written (no second block, no child, the block's outcome and the council section as seeded). The prose under test is `skills/plan/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

`fixtures/scaffold.sh` seeds the `ready` spec `totals-report` (two criteria: the tool prints the totals of a CSV input; the tool prints a one-line summary), the slice parent `print-the-totals-of-a-csv-input` (`in-progress`, label `craft/slice-parent`, linked to the spec) carrying its value claim, one `## Unattended run` block from an earlier plan session, the plan sections (`**Goal:**`, `**Delta design:**`, Axioms, Unknowns, `## Flow-out`) and a `## Council Review`, and the three child tasks `read-the-rows-of-a-csv-input`, `sum-each-numeric-column`, `print-the-totals-and-a-summary-line` (`ready`, contained by the parent, chained with `depends-on`). The block carries the optional `**Planned tasks:**` line naming the three titles. The earlier block's outcome is `plan-ready` (checkpoint `plan complete`) and the council section's Critical is `resolved`. The prompt runs `/craft:plan task/print-the-totals-of-a-csv-input --unattended` (no `--resume`).

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]`, `max_turns: 120`, `timeout_seconds: 900`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails. If the eval spend cap forces fewer runs than three, the run count is reported as run.
- The baseline runs on the current, unedited prose (`skills/plan/SKILL.md`, `skills/_shared/unattended.md`) before any prose edit, with the case's scaffold exactly as committed.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader. It judges only the last assistant message and reads a question parked on the record as part of the report. No headless run has shown it failing on a real question (operator-accepted limitation); the outcome line and the record state are the primary evidence.
- Record contents are graded with `source: file` from the vault files; `file_exists` and `target: files` see only files the agent created, never seeded records. `tool_used` counts Bash calls over the whole run. The regex dialect is JavaScript.
- The run block, the plan and the council section are seeded as a sibling session left them. The plan sections sit between the block and the council section, as plan's full-body write leaves them, so a section boundary in the seeded record is a label, never a heading.

## Pre-registered baseline

The unedited `skills/plan/SKILL.md` has no resume: it treats a latest block as an earlier session's, leaves it, opens a new block and plans again from step 1, and it has no applied-answer or re-park syntax at all. Expected red, certain: `final-line-is-refused`, `exactly-one-outcome-line`, `no-second-block-opened`, `block-outcome-unchanged` (a second block adds a second `**Outcome:**` line), `parent-record-unchanged`, `no-task-created`, `no-task-created-by-command`. Expected red, likely: `council-review-not-re-run`, `plan-body-not-rewritten`. Expected green: `block-unchanged` (the seeded block survives), `parent-still-in-progress`. `refusal-names-what-is-finished` is an `llm` grader, reviewed by reading.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The last line is `outcome: refused task/<name> parked=0 — <why naming what is finished>` | `final-line-is-refused` | regex over the final message |
| The final message holds exactly one `outcome:` line | `exactly-one-outcome-line` | regex over the final message |
| The record still has exactly one block | `no-second-block-opened` | regex, file |
| The block's single `**Outcome:**` is still `plan-ready` | `block-outcome-unchanged` | regex, file |
| Exactly one `## Council Review` heading | `council-review-not-re-run` | regex, file |
| No task record was created | `no-task-created` | file_exists, exists false |
| No `record create --kind task` command ran | `no-task-created-by-command` | tool_used, max 0 |
| The block is byte for byte as seeded | `block-unchanged` | regex, file |
| The parent record was not updated at all (updated-at equals created-at) | `parent-record-unchanged` | regex, file |
| `**Goal:**` and `**Delta design:**` are byte for byte as seeded | `plan-body-not-rewritten` | regex, file |
| The refusal says the latest run is finished | `refusal-names-what-is-finished` | llm, trace |
| The final message poses no question | `asked-nothing` | llm, trace |
| No `/craft:execute` command is printed | `no-execute-command-printed` | regex, not_contains |
| The parent is still `in-progress` | `parent-still-in-progress` | regex, file |

## Limitations

- The `asked-nothing` and any other `llm` grader read only the first and last 12 messages of the trace.
- One fixture, one phrasing, one model tier.
- Leak check, run on the prompt and `fixtures/scaffold.sh` before commit: a case-insensitive search for `resum`, `taken over`, `refus`, `re-park`, `pointer`, `still open`, `applied`, `unmatched`, `expected`, `baseline` finds only the `--resume` token in the two prompts that measure it and, in one fixture, the word `refuse` inside an option the fixture itself parks. The records show the state an earlier session left, not the rule or the outcome measured.
