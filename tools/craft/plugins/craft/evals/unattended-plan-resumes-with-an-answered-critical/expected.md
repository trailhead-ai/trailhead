# unattended-plan-resumes-with-an-answered-critical

## Under test

`/craft:plan task/<slice> --unattended` on a slice whose plan is written, whose children all exist and whose council review holds one Critical with no disposition, parked by the earlier run as `Q1` with an operator `**Answer:** (a)` written under it. The resumed run opens its own block, applies the answered option by a confined edit that dispositions the Critical, logs the answer as applied (`- Applied: Q1` under a decision of its block), creates no child, does not rewrite the plan body, does not run the council again and ends `outcome: plan-ready task/<name> parked=0` (AC44). The prose under test is `skills/plan/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

`fixtures/scaffold.sh` seeds the `ready` spec `totals-report` (two criteria: the tool prints the totals of a CSV input; the tool prints a one-line summary), the slice parent `print-the-totals-of-a-csv-input` (`in-progress`, label `craft/slice-parent`, linked to the spec) carrying its value claim, one `## Unattended run` block from an earlier plan session, the plan sections (`**Goal:**`, `**Delta design:**`, Axioms, Unknowns, `## Flow-out`) and a `## Council Review`, and the three child tasks `read-the-rows-of-a-csv-input`, `sum-each-numeric-column`, `print-the-totals-and-a-summary-line` (`ready`, contained by the parent, chained with `depends-on`). The block carries the optional `**Planned tasks:**` line naming the three titles. The earlier block ended `needs-a-decision` with `Q1` (what the tool does on a non-numeric value: stop with an error naming the row, recommended, or skip the row), and the Critical in the council section has no disposition. The answer is the option letter `(a)`. The prompt runs `/craft:plan task/print-the-totals-of-a-csv-input --unattended`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]`, `max_turns: 120`, `timeout_seconds: 900`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails. If the eval spend cap forces fewer runs than three, the run count is reported as run.
- The baseline runs on the current, unedited prose (`skills/plan/SKILL.md`, `skills/_shared/unattended.md`) before any prose edit, with the case's scaffold exactly as committed.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader that is not shown able to fail in a headless run (operator-accepted limitation); the outcome line and the record state are the primary evidence.
- Record contents are graded with `source: file` from the vault files; `file_exists` and `target: files` see only files the agent created, never seeded records. `tool_used` counts Bash calls over the whole run. The regex dialect is JavaScript.
- The run block, the plan and the council section are seeded as a sibling session left them. The plan sections sit between the block and the council section, as plan's full-body write leaves them, so a section boundary in the seeded record is a label, never a heading.

## Pre-registered baseline

The unedited `skills/plan/SKILL.md` has no resume: it treats a latest block as an earlier session's, leaves it, opens a new block and plans again from step 1, and it has no applied-answer or re-park syntax at all. Expected red, certain: `answer-logged-as-applied` (the unedited prose never writes an applied marker), `no-child-task-created` and `no-child-created-by-command` (it creates the children again). Expected red, likely: `plan-body-not-rewritten` (the plan text is regenerated), `council-review-not-re-run` and `council-review-stamp-kept` (a second council is dispatched). Expected green on the baseline, so not what the case turns on: `earlier-block-preserved-byte-for-byte` (the full-body write keeps every block), `final-line-is-plan-ready`, `block-outcome-is-plan-ready`, `parent-still-in-progress`. If the baseline stays green on the certain clauses the case is a guard, not a detector, and that is recorded as green.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The Critical's line now carries `*Disposition:* `resolved`` | `critical-is-dispositioned-resolved` | regex, file |
| A later block logs `- Applied: Q1` | `answer-logged-as-applied` | regex, file |
| The new block carries `**Outcome:** plan-ready` | `block-outcome-is-plan-ready` | regex, file |
| The last line is `outcome: plan-ready task/<name> parked=0` | `final-line-is-plan-ready` | regex over the final message |
| The final message holds exactly one `outcome:` line | `exactly-one-outcome-line` | regex over the final message |
| No task record was created | `no-child-task-created` | file_exists, exists false |
| No `record create --kind task` command ran | `no-child-created-by-command` | tool_used, max 0 |
| The earlier block, answer included, is byte for byte as seeded | `earlier-block-preserved-byte-for-byte` | regex, file |
| `**Goal:**` and the start of the seeded `**Delta design:**` are kept; only a wholesale rewrite fails | `plan-body-not-rewritten` | regex, file |
| Exactly one `## Council Review` heading | `council-review-not-re-run` | regex, file |
| The council section's reviewed-at stamp is the seeded one | `council-review-stamp-kept` | regex, file |
| The edit follows option (a), not (b) | `applies-the-answered-option` | llm, trace |
| The final message poses no question | `asked-nothing` | llm, trace |
| No `/craft:execute` command is printed | `no-execute-command-printed` | regex, not_contains |
| The parent is still `in-progress` | `parent-still-in-progress` | regex, file |

## Limitations

- The `asked-nothing` and any other `llm` grader read only the first and last 12 messages of the trace.
- One fixture, one phrasing, one model tier.
- Leak check, run on the prompt and `fixtures/scaffold.sh` before commit: a case-insensitive search for `resum`, `taken over`, `refus`, `re-park`, `pointer`, `still open`, `applied`, `unmatched`, `expected`, `baseline` finds only the `--resume` token in the two prompts that measure it and, in one fixture, the word `refuse` inside an option the fixture itself parks. The records show the state an earlier session left, not the rule or the outcome measured.

## Amended after the treatment run

2026-11-16: `plan-body-not-rewritten` pinned `**Delta design:**` byte for byte, but applying answer (a) legitimately appends a sentence to it through the confined `--diff`, so a correct run failed. The fault was in the grader, not a moved bar: it now anchors on `**Goal:**` and the opening of the seeded Delta design text, so an append is allowed and a rewrite still fails.
