# checks-walk-saves-each-answer

## Under test

Every pending check is offered in list order; each answer the operator picks is saved at once through the answer script with the entry's own keys and revision and the operator's words; a Skip is not saved. The prose under test is `skills/checks/SKILL.md` (the walk-through after the list: the design in `docs/design/answer-pending-checks-on-the-check-list.md`). Operator replies cannot be given interactively: a headless eval session never offers `AskUserQuestion`, and the runner has no multi-turn driver. As in earlier cases (`close-defers-an-unconfirmed-check`), the prompt says the operator is not at the keyboard and gives their replies in their own words, in the order the walk asks for them. The skill's fallback, asking in plain text and reading the operator's typed reply as their choice when the choice prompt is unavailable, is what the scripted replies exercise. Stand-in scripts `./gather` and `./answer` replace the plugin's scripts: `./answer` appends one line per call to `answer.log` (keys, answer, `--base`, and the contents of the words file, which it then deletes) and returns the result scripted in `answer.script`. The graders read that log and the issued commands, not the wording of the final message.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, 3 runs, `-j 1`, `--scaffold`, grants as the `runner` block of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Bash, Write]`, `max_turns: 40`, `timeout_seconds: 420`.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 2 of 3 runs. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such.
- The case is first run against the read-only prose, which never calls the answer script, and must fail there. That red is recorded in `tools/craft/MANUAL-EVAL.md`.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| Skill used | `skill-used` | `tool_used` over the issued commands: 'craft:checks' |
| Json gathered | `json-gathered` | `tool_used` over the issued commands: 'gather --json' |
| Answer called 2 times | `answer-called-2-times` | `tool_used` over the issued commands: '\./answer' |
| Call 1 is ac2 passed | `call-1-is-AC2-passed` | `regex` over `answer.log`: 'call 1: vault=levr spec=spec/[a-z-]+ criterion=AC2 slice=[^\n ]+ answer=passed base=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa words=\n' |
| Call 2 is ac9 failed | `call-2-is-AC9-failed` | `regex` over `answer.log`: 'call 2: vault=levr spec=spec/[a-z-]+ criterion=AC9 slice=[^\n ]+ answer=failed base=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb words=the tax line is missing for a UK address\n' |
| No third call | `no-third-call` | `regex` over `answer.log` (must be absent): 'call 3' |
| Skipped entry gets no call | `skipped-entry-gets-no-call` | `regex` over `answer.log` (must be absent): 'criterion=AC12' |
| Words written with the file tool | `words-written-with-the-file-tool` | `tool_used` over the issued commands:  |
| Words not on a command line | `words-not-on-a-command-line` | `tool_used` over the issued commands (must be absent): 'tax line is missing' |

## Limitations

- One fixture, one prompt phrasing, one model tier, three runs.
- The replies are scripted in the prompt, so the case shows how the walk uses them, not how the live choice prompt behaves.
- The stand-in `./answer` is not the real writer; the real one is pinned by `tools/craft/tests/test_checklist_answer.py`.
The operator's words reach the answer script by file, never on a command line.
