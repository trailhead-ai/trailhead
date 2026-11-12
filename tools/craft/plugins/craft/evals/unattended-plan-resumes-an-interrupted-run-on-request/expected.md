# unattended-plan-resumes-an-interrupted-run-on-request

## Under test

`/craft:plan task/<slice> --unattended --resume` on a slice whose earlier plan run left no outcome: the plan sections are written, the block's `**Planned tasks:**` line names two titles and only the first child exists. The caller's `--resume` states the earlier run is over, so the run takes the block over, creates only the missing child (re-derived from the plan's Delta design), does not rewrite the plan body and carries on to the council (AC47). The prose under test is `skills/plan/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

`fixtures/scaffold.sh` seeds the `ready` spec `totals-report`, the slice parent (`in-progress`, label `craft/slice-parent`) carrying its value claim, one outcome-less `## Unattended run` block from an earlier plan session (checkpoint `creating the child tasks`, `**Planned tasks:**` naming `Sum each numeric column` and `Print the totals and a summary line`), the plan sections for a two-part design, no council section, and one child task, `sum-each-numeric-column`. The prompt runs `/craft:plan task/print-the-totals-of-a-csv-input --unattended --resume`. The same record without the token is the fixture of `unattended-plan-refuses-an-outcome-less-block`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]`, `max_turns: 120`, `timeout_seconds: 1500`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails. If the eval spend cap forces fewer runs than three, the run count is reported as run.
- The baseline runs on the current, unedited prose (`skills/plan/SKILL.md`, `skills/_shared/unattended.md`) before any prose edit, with the case's scaffold exactly as committed.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader that is not shown able to fail in a headless run (operator-accepted limitation); the outcome line and the record state are the primary evidence.
- Record contents are graded with `source: file` from the vault files; `file_exists` and `target: files` see only files the agent created, never seeded records. `tool_used` counts Bash calls over the whole run. The regex dialect is JavaScript.
- The run block, the plan and the council section are seeded as a sibling session left them. The plan sections sit between the block and the council section, as plan's full-body write leaves them, so a section boundary in the seeded record is a label, never a heading.

## Pre-registered baseline

The unedited `skills/plan/SKILL.md` has no resume: it treats a latest block as an earlier session's, leaves it, opens a new block and plans again from step 1, and it has no applied-answer or re-park syntax at all. It also has no `--resume` token: the token is read as text and changes nothing. Expected red, certain: `exactly-one-child-created` (the run issues one create for every child of its own plan, so at least two). Expected red, likely: `plan-body-not-rewritten`. Expected green: `the-missing-child-exists`, `existing-child-untouched` (unless the run re-creates the existing child, a name clash it may retry), `a-second-block-follows`, `planned-tasks-marker-kept` (the full-body write keeps every block), the outcome clauses.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| Exactly one `record create --kind task` command ran | `exactly-one-child-created` | tool_used, min 1 max 1 |
| The missing child's record was created | `the-missing-child-exists` | file_exists |
| The created child's sidecar names the slice parent | `the-missing-child-is-contained-by-the-parent` | regex, file |
| The existing child's record was not updated | `existing-child-untouched` | regex, file |
| `**Goal:**` and `**Delta design:**` are byte for byte as seeded | `plan-body-not-rewritten` | regex, file |
| A second block follows the earlier one | `a-second-block-follows` | regex, file |
| The `**Planned tasks:**` lines are still on the record | `planned-tasks-marker-kept` | regex, file |
| The last line is a `plan-ready` or `needs-a-decision` outcome line | `final-line-is-plan-ready-or-needs-a-decision` | regex over the final message |
| The final message holds exactly one `outcome:` line | `exactly-one-outcome-line` | regex over the final message |
| The final message poses no question | `asked-nothing` | llm, trace |
| No `/craft:execute` command is printed | `no-execute-command-printed` | regex, not_contains |
| The parent is still `in-progress` | `parent-still-in-progress` | regex, file |

## Limitations

- The `asked-nothing` and any other `llm` grader read only the first and last 12 messages of the trace.
- One fixture, one phrasing, one model tier.
- Leak check, run on the prompt and `fixtures/scaffold.sh` before commit: a case-insensitive search for `resum`, `taken over`, `refus`, `re-park`, `pointer`, `still open`, `applied`, `unmatched`, `expected`, `baseline` finds only the `--resume` token in the two prompts that measure it and, in one fixture, the word `refuse` inside an option the fixture itself parks. The records show the state an earlier session left, not the rule or the outcome measured.
- The runner block of `eval-gate.json` carries no grant for `run_block.py`. The baseline does not need it (the unedited prose never calls it). A run on edited prose that calls it by path is denied by the runner unless a grant is added; this case does not add one.
