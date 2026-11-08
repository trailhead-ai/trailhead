# unattended-slice-refuses-on-a-gate-refusal

## Under test

Under `--unattended`, a ledger gate that refuses ends the run with `outcome: refused <record> parked=0 — <why>; <what to run instead>` and writes nothing, including the reconcile's ledger line. The prose under test is `skills/slice/SKILL.md` and `skills/_shared/unattended.md`.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `word-count-tool` with three criteria and a `## Slices` section holding one line, for the done slice parent `count-lines`. That line claims `covers AC3` while its parent covers AC2, which the ledger gate refuses as `coverage-contradicts-parent`. A second `done` slice parent, `count-words` (covers AC1), has no ledger line, so the reconcile has a line to append. No slice is open. The prompt runs `/craft:slice spec/word-count-tool --unattended`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 60`, `timeout_seconds: 500`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- The baseline runs on the prose as it stands before this change and is expected red: that prose keeps the reconcile's ledger append when a gate refuses.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The last line is `outcome: refused <record> parked=0 — <why>` | `final-line-is-refused` | `regex` over the final message |
| The spec body gained no line for `count-words` | `no-ledger-line-written` | `regex`, `not_contains`, `source: file` on the spec `.md` |
| The composed spec body is written with the file-writing tool | `body-written-with-the-file-tool` | `tool_used` `Write`, `min: 1` |
| No spec text appears on a Bash command line (the body never passes through argv) | `no-spec-text-on-a-command-line` | `tool_used` `Bash`, `input_match` on the spec's title or a criterion's words, `min: 0`, `max: 0` |
| The spec sidecar's `updated-at` still equals `created-at` (nothing wrote the spec) | `spec-sidecar-untouched` | `regex` with a back-reference, `source: file` on the spec `.json` |

## Limitations

- One spec, one gate reason, one phrasing, one model tier, three runs.
