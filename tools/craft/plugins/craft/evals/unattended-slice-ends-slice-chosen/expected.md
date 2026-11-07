# unattended-slice-ends-slice-chosen

## Under test

Given the explicit request `--unattended` on `/craft:slice`, the ritual asks nothing, logs the selection as a decision on the new slice record in a `## Unattended run` block, sets that block's outcome to `slice-chosen`, ends with the typed line `outcome: slice-chosen task/<name> parked=0`, and prints no `/craft:plan` command. The prose under test is `skills/slice/SKILL.md` and `skills/_shared/unattended.md`, as they stand when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `word-count-tool` with two criteria (AC1 and AC2) and no `## Slices` ledger and no slice. The prompt runs `/craft:slice spec/word-count-tool --unattended`. The headless session has no person to answer, which is the condition a ritual that infers its mode from absence would trip on; the request is explicit in the invocation.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns: 60`, `timeout_seconds: 500`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- The baseline runs on the current, unedited prose before any prose edit and is expected red (the clauses naming the unattended outcome and record state cannot hold on prose that has no unattended branch).
- A headless eval session never offers `AskUserQuestion` (found by the known-unknown probe for this plan), so a `tool_used AskUserQuestion` grader would pass whether or not the ritual wanted to ask. "Asked nothing" is graded by behaviour: the run reaches its typed outcome line, writes the expected record state, and an `llm` trace grader confirms the last message poses no question and waits on no answer.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The final message poses no question and waits on no answer | `asked-nothing` | `llm`, `focus: trace` |
| The last line is `outcome: slice-chosen task/<name> parked=0` | `final-line-is-slice-chosen` | `regex` over the final message |
| The created slice record carries `## Unattended run` with `**Format:** 1`, `**Outcome:** slice-chosen` and a decision entry giving question, choice and reasoning | `run-block-on-created-record` | `llm`, `target: files` |
| No `/craft:plan task/...` command line is printed | `no-plan-command-printed` | `regex`, `not_contains`, over the final message |

## Limitations

- One spec, one phrasing, one model tier, three runs.
- "Asked nothing" is behavioural and judged by a model over the first and last 12 events of the trace.
- The session cannot answer, so a ritual that asked and stopped would end without the outcome line and fail the second clause.
