# plan-keeps-an-earlier-run-section

## Under test

`/craft:plan task/<slice> --unattended` on a slice parent that already carries a closed `## Unattended run` block (outcome `needs-a-decision`, a parked `Q1`) keeps that block byte for byte through step 8's full-body parent write, appends a second block after it, and numbers any question it parks `Q2` or later. The prose under test is `skills/plan/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

`fixtures/scaffold.sh` seeds the `ready` spec `report-export` and the slice parent `write-totals-to-a-named-report-file` (`in-progress`, label `craft/slice-parent`, linked to the spec) whose body carries the value claim and an earlier block with a parked `Q1`. The prompt runs `/craft:plan task/write-totals-to-a-named-report-file --unattended`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent]`, `max_turns: 120`, `timeout_seconds: 900`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails. If the eval spend cap of the task forces fewer runs than three, the run count is reported as run.
- The baseline runs on the current, unedited prose before any prose edit. It is expected red on the preservation and second-block clauses: the unedited full-body write preserves only the value claim and enumerated states.
- A headless eval session never offers `AskUserQuestion`, so a `tool_used AskUserQuestion` grader would pass whether or not the ritual wanted to ask. "Asked nothing" is graded by behaviour: the run reaches its typed outcome line, writes the expected record state, and an `llm` trace grader confirms the last message poses no question and waits on no answer. That `llm` grader is not shown able to fail in a headless run (operator-accepted limitation); the outcome line and the record and command evidence are the primary evidence.
- The `llm` and regex graders read the last message; the regex dialect is JavaScript.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The earlier block is still present byte for byte | `earlier-block-preserved-byte-for-byte` | `regex`, `source: file` |
| A second block follows it | `a-second-block-follows` | `regex`, `source: file` |
| No question is numbered `Q1` a second time | `no-question-number-reused` | `regex`, `not_contains`, `source: file` |
| The last line is an outcome line | `final-line-is-an-outcome-line` | `regex` over the final message |

## Limitations

- The `Q2`-or-later clause is graded as "`Q1` is not reused": a run that parks nothing passes it vacuously. A parked question is not forced by this fixture.
- One fixture, one phrasing, one model tier.
