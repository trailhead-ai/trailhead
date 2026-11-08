# unattended-slice-reports-spec-complete

## Under test

Under `--unattended`, when every acceptance criterion is already covered, `/craft:slice` marks the loop complete and ends with `outcome: spec-complete spec/<name> parked=0`, printing no `/craft:tidy-followups` command and writing no run block. The prose under test is `skills/slice/SKILL.md` and `skills/_shared/unattended.md`; the attended handoff is covered by `slice-spec-complete-hands-off-to-tidy-followups`.

## Fixture

The fixture of `slice-spec-complete-hands-off-to-tidy-followups` (the `export-invoices` spec, a ledger covering AC1 to AC3, two `done` slice parents). The prompt runs `/craft:slice spec/export-invoices --unattended`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 60`, `timeout_seconds: 500`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- The baseline runs on the current, unedited prose before any prose edit and is expected red (the clauses naming the unattended outcome and record state cannot hold on prose that has no unattended branch).
- A headless eval session never offers `AskUserQuestion` (found by the known-unknown probe for this plan), so a `tool_used AskUserQuestion` grader would pass whether or not the ritual wanted to ask. "Asked nothing" is graded by behaviour: the run reaches its typed outcome line, writes the expected record state, and an `llm` trace grader confirms the last message poses no question and waits on no answer.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The last line is `outcome: spec-complete spec/export-invoices parked=0` | `final-line-is-spec-complete` | `regex` over the final message |
| No line begins with a `/craft:tidy-followups` command | `no-tidy-followups-command` | `regex`, `not_contains` |
| The spec sidecar carries `craft/slice-loop` = `complete` | `spec-loop-marked-complete` | `regex`, `source: file` |

## Limitations

- One spec, one phrasing, one model tier, three runs. The marker grader is green on the baseline by design; it shows each run reached the termination path.
