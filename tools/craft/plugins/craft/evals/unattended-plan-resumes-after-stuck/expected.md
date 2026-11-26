# unattended-plan-resumes-after-stuck

## Under test

`/craft:plan task/<slice> --unattended` on a slice whose earlier plan run ended `stuck` at the council dispatch: the plan sections are written, all three children exist (the block's `**Planned tasks:**` line names the same three titles), and there is no `## Council Review`. No `--resume` is given; a latest block that ended `stuck` resumes on its own. The resumed run opens its own block, leaves the earlier block as it is, runs the council and writes `## Council Review`, does not rewrite the plan body and creates no child (AC46). The run ends `plan-ready` or, if the council parks a Critical, `needs-a-decision`; the case measures the resume point, not the council's verdict. The prose under test is `skills/plan/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

`fixtures/scaffold.sh` seeds the `ready` spec `totals-report` (two criteria: the tool prints the totals of a CSV input; the tool prints a one-line summary), the slice parent `print-the-totals-of-a-csv-input` (`in-progress`, label `craft/slice-parent`, linked to the spec) carrying its value claim, one `## Unattended run` block from an earlier plan session, the plan sections (`**Goal:**`, `**Delta design:**`, Axioms, Unknowns, `## Flow-out`) , and the three child tasks `read-the-rows-of-a-csv-input`, `sum-each-numeric-column`, `print-the-totals-and-a-summary-line` (`ready`, contained by the parent, chained with `depends-on`). The block carries the optional `**Planned tasks:**` line naming the three titles. The print task is the command entry point, so the plan wires the parts into one command and the council has no gap there to raise. There is no council section. The earlier block's outcome is `stuck` and its checkpoint says the child tasks are created and the council dispatch stopped; it parks nothing.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]`, `max_turns: 120`, `timeout_seconds: 1500`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails. If the eval spend cap forces fewer runs than three, the run count is reported as run.
- The baseline runs on the current, unedited prose (`skills/plan/SKILL.md`, `skills/_shared/unattended.md`) before any prose edit, with the case's scaffold exactly as committed.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader. It judges only the last assistant message and reads a question parked on the record as part of the report. No headless run has shown it failing on a real question (operator-accepted limitation); the outcome line and the record state are the primary evidence.
- Record contents are graded with `source: file` from the vault files; `file_exists` and `target: files` see only files the agent created, never seeded records. `tool_used` counts Bash calls over the whole run. The regex dialect is JavaScript.
- The run block, the plan and the council section are seeded as a sibling session left them. The plan sections sit between the block and the council section, as plan's full-body write leaves them, so a section boundary in the seeded record is a label, never a heading.

## Pre-registered baseline

The unedited `skills/plan/SKILL.md` has no resume: it treats a latest block as an earlier session's, leaves it, opens a new block and plans again from step 1, and it has no applied-answer or re-park syntax at all. Expected red, certain: `no-child-task-created` and `no-child-created-by-command` (the three children are created a second time). Expected red, likely: `plan-body-not-rewritten`. Expected green, so not what the case turns on: `council-review-written`, `a-second-block-follows`, `earlier-block-preserved-byte-for-byte`, the outcome clauses. The baseline is a detector only through the child and plan-body clauses.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| A `## Council Review` with a members line is on the parent | `council-review-written` | regex, file |
| `**Goal:**` and `**Delta design:**` are byte for byte as seeded, except that sentences may follow the seeded design on its line (a council Critical closed by a design rule) | `plan-body-not-rewritten` | regex, file |
| No task record was created | `no-child-task-created` | file_exists, exists false |
| No `record create --kind task` command ran | `no-child-created-by-command` | tool_used, max 0 |
| The earlier block is byte for byte as seeded | `earlier-block-preserved-byte-for-byte` | regex, file |
| A second block follows the earlier one | `a-second-block-follows` | regex, file |
| The second block carries `plan-ready` or `needs-a-decision` | `second-block-has-an-outcome` | regex, file |
| The last line is a `plan-ready` or `needs-a-decision` outcome line | `final-line-is-plan-ready-or-needs-a-decision` | regex over the final message |
| The final message holds exactly one `outcome:` line | `exactly-one-outcome-line` | regex over the final message |
| The final message poses no question | `asked-nothing` | llm, trace |
| No `/craft:execute` command is printed | `no-execute-command-printed` | regex, not_contains |
| The parent is still `in-progress` | `parent-still-in-progress` | regex, file |

## Limitations

- The `asked-nothing` and any other `llm` grader read only the first and last 12 messages of the trace.
- One fixture, one phrasing, one model tier.
- Leak check, run on the prompt and `fixtures/scaffold.sh` before commit: a case-insensitive search for `resum`, `taken over`, `refus`, `re-park`, `pointer`, `still open`, `applied`, `unmatched`, `expected`, `baseline` finds only the `--resume` token in the two prompts that measure it and, in one fixture, the word `refuse` inside an option the fixture itself parks. The records show the state an earlier session left, not the rule or the outcome measured.

## Amended after the treatment run

2026-11-16: the seeded plan had no task wiring the reader, summer and printer into a command, so the council legitimately raised a Critical and the run added a child task. The fault was in the fixture, not a moved bar: the print task is now the command entry point and the Delta design says so, and `plan-body-not-rewritten` pins that text, so no Critical arises from a gap in the seeded plan.
