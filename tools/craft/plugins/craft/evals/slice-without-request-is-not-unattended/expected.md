# slice-without-request-is-not-unattended

## Under test

Without the explicit `--unattended` request, `/craft:slice` behaves as it always has even when the session is headless and no person is present: it prints no `outcome:` line, writes no `## Unattended run` block, and ends with the `/craft:plan task/<name>` handoff. This is the "never inferred from absence" check. The prose under test is `skills/slice/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

The same `word-count-tool` fixture as `unattended-slice-ends-slice-chosen`; the prompt runs `/craft:slice spec/word-count-tool` with no flag.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns: 60`, `timeout_seconds: 500`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- The baseline runs on the current, unedited prose before any prose edit and is expected red (the clauses naming the unattended outcome and record state cannot hold on prose that has no unattended branch).
- A headless eval session never offers `AskUserQuestion` (found by the known-unknown probe for this plan), so a `tool_used AskUserQuestion` grader would pass whether or not the ritual wanted to ask. "Asked nothing" is graded by behaviour: the run reaches its typed outcome line, writes the expected record state, and an `llm` trace grader confirms the last message poses no question and waits on no answer.

The baseline is expected green: unedited prose has no unattended branch to trip, so this case is a guard against the edit, not a detector.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| No line of the final message begins with `outcome:` | `no-outcome-line` | `regex`, `not_contains` |
| A slice record was created (the run reached materialization) | `a-slice-was-created` | `file_exists` on `vault/task/*.md` |
| No Bash call mentions `## Unattended run` | `no-run-block-written` | `tool_used` Bash, `min: 0`, `max: 0` |
| The `/craft:plan task/<name>` handoff is printed | `plan-handoff-printed` | `regex` over the final message |

## Limitations

- The absence of a block is graded from the commands the run issued (`tool_used`), since a grader over the created record's contents is not available (see `unattended-slice-ends-slice-chosen`).

- One spec, one phrasing, one model tier, three runs.
