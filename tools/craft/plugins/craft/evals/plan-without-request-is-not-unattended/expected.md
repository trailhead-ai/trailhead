# plan-without-request-is-not-unattended

## Under test

Without the explicit `--unattended` request, `/craft:plan task/<slice>` behaves as it always has even when the session is headless: it prints no `outcome:` line, writes no `## Unattended run` block, stops at the approval step with the `/craft:execute task/<name>` handoff, and marks no council finding as decided unattended. This is the "never inferred from absence" check for plan. The prose under test is `skills/plan/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

The seeded `report-export` spec and slice parent of `plan-keeps-an-earlier-run-section`, without an earlier block. The prompt runs `/craft:plan task/write-totals-to-a-named-report-file` with no flag and states the operator's approval of the recommended approach and of every recommended council disposition, so the attended run can reach its approval step headless; it says nothing about unattended mode.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent]`, `max_turns: 120`, `timeout_seconds: 900`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails. If the eval spend cap of the task forces fewer runs than three, the run count is reported as run.
- The baseline runs on the current, unedited prose before any prose edit. It is expected green: unedited prose has no unattended branch to trip, so this is a guard against the edit, not a detector.
- A headless eval session never offers `AskUserQuestion`, so a `tool_used AskUserQuestion` grader would pass whether or not the ritual wanted to ask. "Asked nothing" is graded by behaviour: the run reaches its typed outcome line, writes the expected record state, and an `llm` trace grader confirms the last message poses no question and waits on no answer. That `llm` grader is not shown able to fail in a headless run (operator-accepted limitation); the outcome line and the record and command evidence are the primary evidence.
- The `llm` and regex graders read the last message; the regex dialect is JavaScript.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| No line of the final message begins with `outcome:` | `no-outcome-line` | `regex`, `not_contains` |
| The slice parent's body carries no `## Unattended run` heading, whichever tool wrote it | `no-run-block-written` | `regex`, `not_contains`, `source: file` on `vault/task/write-totals-to-a-named-report-file.md` |
| The `/craft:execute task/<name>` handoff is printed | `execute-handoff-printed` | `regex` over the final message |
| The council ran and its section is on the parent | `council-review-reached` | `regex`, `source: file` |
| No `## Council Review` entry is marked decided unattended | `no-critical-marked-decided-unattended` | `regex`, `not_contains`, `source: file` |

## Limitations

- The absence graders are not shown able to fail on a one-run prose mutation (the model follows the redundant text); the handoff and council-reached clauses show the run reached its approval step.
- One fixture, one phrasing, one model tier.
