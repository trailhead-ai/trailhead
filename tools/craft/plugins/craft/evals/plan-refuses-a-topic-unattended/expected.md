# plan-refuses-a-topic-unattended

## Under test

`/craft:plan <a topic> --unattended` is a refusal: the unattended request is honoured only on the slice-rooted path, so a topic ends `outcome: refused <name> parked=0 — <why>; <what to run instead>`, writes nothing, and does not echo the topic into the outcome line. The prose under test is `skills/plan/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

`fixtures/scaffold.sh` seeds only the lore vault and its placeholder record: no spec, no task. The prompt runs `/craft:plan add a CSV export for invoices --unattended`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep]`, `max_turns: 40`, `timeout_seconds: 420`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails. If the eval spend cap of the task forces fewer runs than three, the run count is reported as run.
- The baseline runs on the current, unedited prose before any prose edit. It is expected red on the final-line clause: unedited plan has no refused outcome.
- A headless eval session never offers `AskUserQuestion`, so a `tool_used AskUserQuestion` grader would pass whether or not the ritual wanted to ask. "Asked nothing" is graded by behaviour: the run reaches its typed outcome line, writes the expected record state, and an `llm` trace grader confirms the last message poses no question and waits on no answer. That `llm` grader is not shown able to fail in a headless run (operator-accepted limitation); the outcome line and the record and command evidence are the primary evidence.
- The `llm` and regex graders read the last message; the regex dialect is JavaScript.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The last line is `outcome: refused <name> parked=0 — <why>; <what to do instead>` | `final-line-is-refused` | `regex` over the final message |
| No task record exists | `no-task-created` | `file_exists`, `exists: false` on `vault/task/*.md` |
| The outcome line does not repeat the topic | `refusal-line-does-not-echo-the-topic` | `regex`, `not_contains` |

## Limitations

- "Writes nothing" is graded as no task record; the vault's other record kinds are not enumerated.
- A refusal on a task that is not a slice parent is not a separate case.
- The no-echo clause is vacuous on a run with no outcome line.
