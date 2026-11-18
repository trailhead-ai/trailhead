# unattended-plan-creates-only-the-missing-planned-children

## Under test

`/craft:plan task/<slice> --unattended` on a slice whose earlier plan run ended `stuck` while creating the children: the plan sections are written, the block's `**Planned tasks:**` line names three titles in order, and the first two children exist. The resumed run compares the titles with `lore task graph` and creates only the third, its body re-derived from the parent's Delta design; it does not rewrite the plan body, touches neither existing child, and carries on to the council (the KU1 marker, AC47). The prose under test is `skills/plan/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

`fixtures/scaffold.sh` seeds the `ready` spec `totals-report`, the slice parent (`in-progress`, label `craft/slice-parent`) carrying its value claim, one `## Unattended run` block (outcome `stuck`, checkpoint `creating the child tasks`, `**Planned tasks:**` naming `Read the rows of a CSV input`, `Sum each numeric column`, `Print the totals and a summary line`), the plan sections for a three-part design, no council section, and the children `read-the-rows-of-a-csv-input` and `sum-each-numeric-column`. The prompt runs `/craft:plan task/print-the-totals-of-a-csv-input --unattended`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]`, `max_turns: 120`, `timeout_seconds: 1500`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails. If the eval spend cap forces fewer runs than three, the run count is reported as run.
- The baseline runs on the current, unedited prose (`skills/plan/SKILL.md`, `skills/_shared/unattended.md`) before any prose edit, with the case's scaffold exactly as committed.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader. It judges only the last assistant message and reads a question parked on the record as part of the report. No headless run has shown it failing on a real question (operator-accepted limitation); the outcome line and the record state are the primary evidence.
- Record contents are graded with `source: file` from the vault files; `file_exists` and `target: files` see only files the agent created, never seeded records. `tool_used` counts Bash calls over the whole run. The regex dialect is JavaScript.
- The run block, the plan and the council section are seeded as a sibling session left them. The plan sections sit between the block and the council section, as plan's full-body write leaves them, so a section boundary in the seeded record is a label, never a heading.

## Pre-registered baseline

The unedited `skills/plan/SKILL.md` has no resume: it treats a latest block as an earlier session's, leaves it, opens a new block and plans again from step 1, and it has no applied-answer or re-park syntax at all. The unedited prose does not write or read a `**Planned tasks:**` line. Expected red, certain: `exactly-one-child-created` (it issues one create for each child of its own plan, so three). Expected red, likely: `plan-body-not-rewritten`. Expected green: `the-missing-child-exists` (a re-created third child has the same or a suffixed name), the existing-child clauses unless the run re-creates them, `earlier-block-preserved-byte-for-byte`, the outcome clauses. The case turns on the create count.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| Exactly one `record create --kind task` command ran | `exactly-one-child-created` | tool_used, min 1 max 1 |
| The missing third child's record exists | `the-missing-child-exists` | file_exists |
| The third child's body has Delivers, Test contract and Files | `the-missing-child-body-follows-the-plan` | regex, file |
| The third child's sidecar names the slice parent | `the-missing-child-is-contained-by-the-parent` | regex, file |
| The first child's record was not updated | `first-existing-child-untouched` | regex, file |
| The second child's record was not updated | `second-existing-child-untouched` | regex, file |
| `**Goal:**` and `**Delta design:**` are byte for byte as seeded, except that sentences may follow the seeded design on its line (a council Critical closed by a design rule) | `plan-body-not-rewritten` | regex, file |
| The earlier block, marker included, is byte for byte as seeded | `earlier-block-preserved-byte-for-byte` | regex, file |
| A second block follows the earlier one | `a-second-block-follows` | regex, file |
| The last line is a `plan-ready` or `needs-a-decision` outcome line | `final-line-is-plan-ready-or-needs-a-decision` | regex over the final message |
| The final message holds exactly one `outcome:` line | `exactly-one-outcome-line` | regex over the final message |
| The final message poses no question | `asked-nothing` | llm, trace |
| No `/craft:execute` command is printed | `no-execute-command-printed` | regex, not_contains |
| The parent is still `in-progress` | `parent-still-in-progress` | regex, file |

## Limitations

- The `asked-nothing` and any other `llm` grader read only the first and last 12 messages of the trace.
- One fixture, one phrasing, one model tier.
- Leak check, run on the prompt and `fixtures/scaffold.sh` before commit: a case-insensitive search for `resum`, `taken over`, `refus`, `re-park`, `pointer`, `still open`, `applied`, `unmatched`, `expected`, `baseline` finds only the `--resume` token in the two prompts that measure it and, in one fixture, the word `refuse` inside an option the fixture itself parks. The records show the state an earlier session left, not the rule or the outcome measured.
