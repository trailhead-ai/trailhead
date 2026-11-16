# checks-three-stale-refusals-in-a-row-move-on

## Under test

After three stale refusals in a row for one entry the walk names it, leaves it waiting and moves on to the next; it makes no fourth attempt. The prose under test is `skills/checks/SKILL.md` (the walk-through after the list: the design in `docs/design/answer-pending-checks-on-the-check-list.md`). Operator replies cannot be given interactively: a headless eval session never offers `AskUserQuestion`, and the runner has no multi-turn driver. As in earlier cases (`close-defers-an-unconfirmed-check`), the prompt says the operator is not at the keyboard and gives their replies in their own words, in the order the walk asks for them. The skill's fallback, asking in plain text and reading the operator's typed reply as their choice when the choice prompt is unavailable, is what the scripted replies exercise. Stand-in scripts `./gather` and `./answer` replace the plugin's scripts: `./answer` appends one line per call to `answer.log` (keys, answer, `--base`, and the contents of the words file, which it then deletes) and returns the result scripted in `answer.script`. The graders read that log and the issued commands, not the wording of the final message.

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
| Answer called 4 times | `answer-called-4-times` | `tool_used` over the issued commands: '\./answer' |
| Call 3 is ac2 failed | `call-3-is-AC2-failed` | `regex` over `answer.log`: 'call 3: vault=levr spec=spec/[a-z-]+ criterion=AC2 slice=[^\n ]+ answer=failed base=ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff words=w three\n' |
| Call 4 is the next entry | `call-4-is-the-next-entry` | `regex` over `answer.log`: 'call 4: vault=levr spec=spec/[a-z-]+ criterion=AC9 slice=[^\n ]+ answer=passed base=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb words=\n' |
| No fourth attempt on the stale entry | `no-fourth-attempt-on-the-stale-entry` | `regex` over `answer.log` (must be absent): 'call 4: [^\n]*criterion=AC2' |
| Stale entry is named and left waiting | `stale-entry-is-named-and-left-waiting` | `regex` over the last message: 'AC2[^\n]*waiting' |

## Limitations

- One fixture, one prompt phrasing, one model tier, three runs.
- The replies are scripted in the prompt, so the case shows how the walk uses them, not how the live choice prompt behaves.
- The stand-in `./answer` is not the real writer; the real one is pinned by `tools/craft/tests/test_checklist_answer.py`.

