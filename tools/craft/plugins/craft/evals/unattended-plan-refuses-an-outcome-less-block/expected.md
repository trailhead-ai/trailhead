# unattended-plan-refuses-an-outcome-less-block

## Under test

`/craft:plan task/<slice> --unattended` without `--resume` on the record of `unattended-plan-resumes-an-interrupted-run-on-request`: the latest block has no outcome, so it is held by another run, live or interrupted, and the run is refused (AC39). It ends `outcome: refused task/<name> parked=0 — <why>; <what to run instead>`, writes nothing (no block, no child, no council section), and its words say that the record's latest block is held, how long ago the record last changed, and that `--resume` is only for an earlier run known to be over. The prose under test is `skills/plan/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

The same seeded record as `unattended-plan-resumes-an-interrupted-run-on-request`, with its own copy of the scaffold. The prompt omits `--resume`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]`, `max_turns: 120`, `timeout_seconds: 900`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails. If the eval spend cap forces fewer runs than three, the run count is reported as run.
- The baseline runs on the current, unedited prose (`skills/plan/SKILL.md`, `skills/_shared/unattended.md`) before any prose edit, with the case's scaffold exactly as committed.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader that is not shown able to fail in a headless run (operator-accepted limitation); the outcome line and the record state are the primary evidence.
- Record contents are graded with `source: file` from the vault files; `file_exists` and `target: files` see only files the agent created, never seeded records. `tool_used` counts Bash calls over the whole run. The regex dialect is JavaScript.
- The run block, the plan and the council section are seeded as a sibling session left them. The plan sections sit between the block and the council section, as plan's full-body write leaves them, so a section boundary in the seeded record is a label, never a heading.

## Pre-registered baseline

The unedited `skills/plan/SKILL.md` has no resume: it treats a latest block as an earlier session's, leaves it, opens a new block and plans again from step 1, and it has no applied-answer or re-park syntax at all. Expected red, certain: `final-line-is-refused`, `exactly-one-outcome-line`, `no-second-block-opened`, `block-left-without-an-outcome`, `parent-body-has-no-council-review`, `parent-record-unchanged`, `no-task-created`, `no-task-created-by-command` (a baseline run plans on and writes all of them). Expected green: `block-unchanged` (the seeded block survives byte for byte), `parent-still-in-progress`. `refusal-words` is an `llm` grader, reviewed by reading.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The last line is `outcome: refused task/<name> parked=0 — <why>` | `final-line-is-refused` | regex over the final message |
| The final message holds exactly one `outcome:` line | `exactly-one-outcome-line` | regex over the final message |
| The record still has exactly one block | `no-second-block-opened` | regex, file |
| No `**Outcome:**` line was written | `block-left-without-an-outcome` | regex, not_contains |
| No council section was written | `parent-body-has-no-council-review` | regex, not_contains |
| No task record was created | `no-task-created` | file_exists, exists false |
| No `record create --kind task` command ran | `no-task-created-by-command` | tool_used, max 0 |
| The block is byte for byte as seeded | `block-unchanged` | regex, file |
| The parent record was not updated at all (updated-at equals created-at) | `parent-record-unchanged` | regex, file |
| The refusal says the block is held, how long ago the record last changed, and what a resume request is for | `refusal-words` | llm, trace |
| The final message poses no question | `asked-nothing` | llm, trace |
| No `/craft:execute` command is printed | `no-execute-command-printed` | regex, not_contains |
| The parent is still `in-progress` | `parent-still-in-progress` | regex, file |

## Limitations

- The `asked-nothing` and any other `llm` grader read only the first and last 12 messages of the trace.
- One fixture, one phrasing, one model tier.
- Leak check, run on the prompt and `fixtures/scaffold.sh` before commit: a case-insensitive search for `resum`, `taken over`, `refus`, `re-park`, `pointer`, `still open`, `applied`, `unmatched`, `expected`, `baseline` finds only the `--resume` token in the two prompts that measure it and, in one fixture, the word `refuse` inside an option the fixture itself parks. The records show the state an earlier session left, not the rule or the outcome measured.
- The runner block of `eval-gate.json` carries no grant for `run_block.py`. The baseline does not need it (the unedited prose never calls it). A run on edited prose that calls it by path is denied by the runner unless a grant is added; this case does not add one.
