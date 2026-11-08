# unattended-slice-refuses-a-draft-spec

## Under test

Under `--unattended`, a spec that is not `ready` is refused with `outcome: refused spec/<name> parked=0 — <why>; <what to run instead>`, writing nothing: no task is created and the spec is untouched. The prose under test is `skills/slice/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

`fixtures/scaffold.sh` seeds a `draft` spec `word-count-tool` with two criteria and no tasks. The prompt runs `/craft:slice spec/word-count-tool --unattended`.

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
| The last line is `outcome: refused spec/word-count-tool parked=0 — <why>` | `final-line-is-refused` | `regex` over the final message |
| No task record exists | `no-task-created` | `file_exists` false on `vault/task/*.md` |
| The spec sidecar's `updated-at` still equals `created-at` | `spec-sidecar-untouched` | `regex` with a back-reference, `source: file` |

## Limitations

- One spec, one phrasing, one model tier, three runs.
