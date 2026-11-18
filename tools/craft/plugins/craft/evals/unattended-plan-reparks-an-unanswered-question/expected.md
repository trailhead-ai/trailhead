# unattended-plan-reparks-an-unanswered-question

## Under test

The same slice state as `unattended-plan-resumes-with-an-answered-critical`, with two Criticals parked by the earlier run as `Q1` and `Q2`; the operator answered `Q1` with the exact text of its option (a) and left `Q2` unanswered. The resumed run applies `Q1` (disposition written, `- Applied: Q1` logged), re-parks `Q2` as the pointer `- Q2: still open — answer goes under the original Q2 entry` in its own block (no second `Q2` entry, no new question), writes no answer of its own and, with nothing else left, ends `outcome: needs-a-decision task/<name> parked=<n>` (AC45). The prose under test is `skills/plan/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

`fixtures/scaffold.sh` seeds the `ready` spec `totals-report` (two criteria: the tool prints the totals of a CSV input; the tool prints a one-line summary), the slice parent `print-the-totals-of-a-csv-input` (`in-progress`, label `craft/slice-parent`, linked to the spec) carrying its value claim, one `## Unattended run` block from an earlier plan session, the plan sections (`**Goal:**`, `**Delta design:**`, Axioms, Unknowns, `## Flow-out`) and a `## Council Review`, and the three child tasks `read-the-rows-of-a-csv-input`, `sum-each-numeric-column`, `print-the-totals-and-a-summary-line` (`ready`, contained by the parent, chained with `depends-on`). The block carries the optional `**Planned tasks:**` line naming the three titles. The earlier block ended `needs-a-decision` with two parked questions: `Q1` (a non-numeric value in a totals column; answered with the text `stop with an error that names the row`) and `Q2` (whether the tool may read a path outside the working directory; unanswered), and the council section holds two Criticals, neither dispositioned.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]`, `max_turns: 120`, `timeout_seconds: 900`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails. If the eval spend cap forces fewer runs than three, the run count is reported as run.
- The baseline runs on the current, unedited prose (`skills/plan/SKILL.md`, `skills/_shared/unattended.md`) before any prose edit, with the case's scaffold exactly as committed.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader. It judges only the last assistant message and reads a question parked on the record as part of the report. No headless run has shown it failing on a real question (operator-accepted limitation); the outcome line and the record state are the primary evidence.
- Record contents are graded with `source: file` from the vault files; `file_exists` and `target: files` see only files the agent created, never seeded records. `tool_used` counts Bash calls over the whole run. The regex dialect is JavaScript.
- The run block, the plan and the council section are seeded as a sibling session left them. The plan sections sit between the block and the council section, as plan's full-body write leaves them, so a section boundary in the seeded record is a label, never a heading.

## Pre-registered baseline

The unedited `skills/plan/SKILL.md` has no resume: it treats a latest block as an earlier session's, leaves it, opens a new block and plans again from step 1, and it has no applied-answer or re-park syntax at all. Expected red, certain: `answered-question-logged-as-applied`, `unanswered-question-reparked-as-a-pointer`, `no-child-task-created`, `no-child-created-by-command`, and `final-line-is-needs-a-decision` with `block-outcome-is-needs-a-decision` (the unedited run ends `plan-ready` or `stuck`, or parks a question of its own). Expected red, likely: `applied-critical-is-dispositioned`, `council-review-not-re-run`. Expected green: `earlier-block-preserved-byte-for-byte`, `no-answer-written-for-the-open-question`, `parent-still-in-progress`; `no-new-question-defined` turns red if the unedited run parks a question of its own (then it is also a note on the run).

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| A later block logs `- Applied: Q1` | `answered-question-logged-as-applied` | regex, file |
| A later block carries the exact pointer line for `Q2` | `unanswered-question-reparked-as-a-pointer` | regex, file |
| The first Critical is dispositioned `resolved` and the second line has no disposition | `applied-critical-is-dispositioned` | regex, file |
| Exactly one `**Answer:**` line is on the record (the seeded one) | `no-answer-written-for-the-open-question` | regex, file |
| No `Q3` entry exists | `no-new-question-defined` | regex, not_contains |
| The new block carries `**Outcome:** needs-a-decision` | `block-outcome-is-needs-a-decision` | regex, file |
| The last line is `outcome: needs-a-decision task/<name> parked=<n>` | `final-line-is-needs-a-decision` | regex over the final message |
| The final message holds exactly one `outcome:` line | `exactly-one-outcome-line` | regex over the final message |
| No task record was created | `no-child-task-created` | file_exists, exists false |
| No `record create --kind task` command ran | `no-child-created-by-command` | tool_used, max 0 |
| The earlier block, answer included, is byte for byte as seeded | `earlier-block-preserved-byte-for-byte` | regex, file |
| Exactly one `## Council Review` heading | `council-review-not-re-run` | regex, file |
| The final message poses no question | `asked-nothing` | llm, trace |
| No `/craft:execute` command is printed | `no-execute-command-printed` | regex, not_contains |
| The parent is still `in-progress` | `parent-still-in-progress` | regex, file |

## Limitations

- The `asked-nothing` and any other `llm` grader read only the first and last 12 messages of the trace.
- One fixture, one phrasing, one model tier.
- Leak check, run on the prompt and `fixtures/scaffold.sh` before commit: a case-insensitive search for `resum`, `taken over`, `refus`, `re-park`, `pointer`, `still open`, `applied`, `unmatched`, `expected`, `baseline` finds only the `--resume` token in the two prompts that measure it and, in one fixture, the word `refuse` inside an option the fixture itself parks. The records show the state an earlier session left, not the rule or the outcome measured.
