# unattended-plan-parks-a-correction-at-the-end

## Under test

`/craft:plan task/<slice> --unattended` on a fresh slice whose design reads an `active` decision that is wrong against the spec: the spec's Input section gives 4 files of 25,000 rows read in chunks of 10,000, while `decision/chunk-count-follows-the-nightly-row-count` says the input is 40,000 rows and 4 chunks. The run plans and reviews as usual, and the correction to the decision is parked as a question when found without stopping the plan; the plan is finished (parent sections written, children created, council section written), the decision record is not edited, and the run ends `outcome: needs-a-decision task/<name> parked=<n>` with `n` at least 1 instead of `plan-ready`. The prose under test is `skills/plan/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

`fixtures/scaffold.sh` seeds the `ready` spec `totals-report` (an Input section giving 4 files of 25,000 rows read in chunks of 10,000 rows and naming the decision), the `active` decision, and the slice parent (`in-progress`, label `craft/slice-parent`) whose value claim is that one command prints the column totals and a one-line summary for the nightly input. No block, no plan, no children.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]`, `max_turns: 120`, `timeout_seconds: 1500`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails. If the eval spend cap forces fewer runs than three, the run count is reported as run.
- The baseline runs on the current, unedited prose (`skills/plan/SKILL.md`, `skills/_shared/unattended.md`) before any prose edit, with the case's scaffold exactly as committed.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader. It judges only the last assistant message and reads a question parked on the record as part of the report. No headless run has shown it failing on a real question (operator-accepted limitation); the outcome line and the record state are the primary evidence.
- Record contents are graded with `source: file` from the vault files; `file_exists` and `target: files` see only files the agent created, never seeded records. `tool_used` counts Bash calls over the whole run. The regex dialect is JavaScript.
- The run block, the plan and the council section are seeded as a sibling session left them. The plan sections sit between the block and the council section, as plan's full-body write leaves them, so a section boundary in the seeded record is a label, never a heading.

## Pre-registered baseline

The unedited `skills/plan/SKILL.md` logs a correction to a record outside the slice as a decision and parks nothing for it, so a run that finds the mismatch ends `plan-ready` with `parked=0`. Expected red, certain on a run that finds the mismatch: `final-line-is-needs-a-decision`, `exactly-one-outcome-line`, `block-outcome-is-needs-a-decision`, `q1-has-two-options-each-with-a-cost-and-a-progress-line`, `q1-is-the-correction`. Expected green: the plan clauses (`plan-was-written-to-the-parent`, `council-review-written`, `a-child-task-was-created`), the decision-record clauses. Not every run need find the mismatch: the baseline transcripts are read to check that a run ending `plan-ready` listed the correction as a decision; a run that never found it is a fixture finding, not a prose result.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The last line is `outcome: needs-a-decision task/<name> parked=<n>`, n at least 1 | `final-line-is-needs-a-decision` | regex over the final message |
| The final message holds exactly one `outcome:` line | `exactly-one-outcome-line` | regex over the final message |
| `Q1` has two options, each with a cost, and a progress line | `q1-has-two-options-each-with-a-cost-and-a-progress-line` | regex, file |
| `Q1` is the correction to the decision | `q1-is-the-correction` | llm, trace |
| The block carries `**Outcome:** needs-a-decision` | `block-outcome-is-needs-a-decision` | regex, file |
| The parent body carries the plan sections | `plan-was-written-to-the-parent` | regex, file |
| A `## Council Review` with a members line is on the parent | `council-review-written` | regex, file |
| A child task was created | `a-child-task-was-created` | tool_used |
| The decision still says `4 chunks` | `decision-record-keeps-its-wrong-figure` | regex, file |
| The decision record was not updated | `decision-record-unchanged` | regex, file |
| No `**Answer:**` line was written | `no-answer-line-written` | regex, not_contains |
| The final message poses no question | `asked-nothing` | llm, trace |
| No `/craft:execute` command is printed | `no-execute-command-printed` | regex, not_contains |
| The parent is still `in-progress` | `parent-still-in-progress` | regex, file |

## Limitations

- The `asked-nothing` and any other `llm` grader read only the first and last 12 messages of the trace.
- One fixture, one phrasing, one model tier.
- Leak check, run on the prompt and `fixtures/scaffold.sh` before commit: a case-insensitive search for `resum`, `taken over`, `refus`, `re-park`, `pointer`, `still open`, `applied`, `unmatched`, `expected`, `baseline` finds only the `--resume` token in the two prompts that measure it and, in one fixture, the word `refuse` inside an option the fixture itself parks. The records show the state an earlier session left, not the rule or the outcome measured.
