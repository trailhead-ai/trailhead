# checks-two-answers-on-one-spec-both-save

## Under test

Two pending checks on the same spec are answered one after the other, and both are saved. The first save moves the spec to a new revision, which the answer script prints as `revision: <rev>` after `saved:`; the second answer must go out with that revision as `--base`, not the gather's. Sending the gather's revision again is refused `stale` and the second answer is lost. The prose under test is `skills/checks/SKILL.md` (Step 3, the paragraph after the answer-script command). Operator replies are given in the prompt, as in `checks-walk-saves-each-answer`, because a headless session has no choice prompt. The stand-in `./answer` keeps one revision per spec: a save moves it to the next value and prints it, and a `--base` that is not the current one is refused `stale` and saves nothing. Each call is logged with its keys, `--base`, the words read from the file, and `result=saved` or `result=stale`. The graders read that log, the issued commands and the final message.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, 3 runs, `-j 1`, `--scaffold`, grants as the `runner` block of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Bash, Write]`, `max_turns: 40`, `timeout_seconds: 420`.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 2 of 3 runs. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such.
- The case is first run against the prose before the `revision:` paragraph existed, which sends the gather's revision for the second answer, and must fail there.
- The case runs outside the authoring sandbox, by the operator.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| Skill used | `skill-used` | `tool_used` over the issued commands: 'craft:checks' |
| Json gathered | `json-gathered` | `tool_used` over the issued commands: 'gather --json' |
| Answer called 2 times | `answer-called-2-times` | `tool_used` over the issued commands: '\./answer' |
| Call 1 is AC2 failed and saved | `call-1-is-AC2-failed-and-saved` | `regex` over `answer.log`: call 1 on AC2, `base=aaaa…`, the first reply's words, `result=saved` |
| Call 2 is AC4 failed with the returned revision and saved | `call-2-is-AC4-failed-with-the-returned-revision-and-saved` | `regex` over `answer.log`: call 2 on AC4, `base=dddd…` (the revision call 1 printed), the second reply's words, `result=saved` |
| No stale refusal | `no-stale-refusal` | `regex` over `answer.log` (must be absent): 'result=stale' |
| No third call | `no-third-call` | `regex` over `answer.log` (must be absent): 'call 3' |
| Summary counts both answers saved | `summary-counts-both-answers-saved` | `regex` over the final message, case-insensitive: '2 answers? saved' |
| No changed-after-the-list-was-read line | `summary-has-no-changed-after-the-list-was-read` | `regex` over the final message (must be absent), case-insensitive: 'changed after the list was read' |
| Words written with the file tool | `words-written-with-the-file-tool` | `tool_used` over the issued commands: Write, at least 2 |
| Words not on a command line | `words-not-on-a-command-line` | `tool_used` over the issued commands (must be absent): the two replies' words |

## Limitations

- One fixture, one prompt phrasing, one model tier, three runs.
- The replies are scripted in the prompt, so the case shows how the walk uses them, not how the live choice prompt behaves.
- The stand-in `./answer` is not the real writer; the real one is pinned by `tools/craft/tests/test_checklist_answer.py`.
- The final-message graders see only the last message, so a `changed after the list was read` line printed earlier in the walk is caught by `no-stale-refusal` in the log, not by the message grader.
