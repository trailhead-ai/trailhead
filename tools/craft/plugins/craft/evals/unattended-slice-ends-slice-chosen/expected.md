# unattended-slice-ends-slice-chosen

## Under test

Given the explicit request `--unattended` on `/craft:slice`, the ritual asks nothing, logs the selection as a decision on the new slice record in a `## Unattended run` block, sets that block's outcome to `slice-chosen`, ends with the typed line `outcome: slice-chosen task/<name> parked=0`, and prints no `/craft:plan` command. The prose under test is `skills/slice/SKILL.md` and `skills/_shared/unattended.md`, as they stand when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `word-count-tool` with two criteria (AC1 and AC2) and no `## Slices` ledger and no slice. The prompt runs `/craft:slice spec/word-count-tool --unattended`. The headless session has no person to answer, which is the condition a ritual that infers its mode from absence would trip on; the request is explicit in the invocation.

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
| The final message poses no question and waits on no answer | `asked-nothing` | `llm`, `focus: trace` |
| The last line is `outcome: slice-chosen task/<name> parked=0` | `final-line-is-slice-chosen` | `regex` over the final message |
| The diff file written for the block carries `## Unattended run` and `**Format:** 1` | `block-open-command-issued` | `tool_used` Write, `min: 1` |
| A `lore record update --diff` command is issued, with the diff read from a file (no block text on the command line) | `block-written-by-diff` | `tool_used` Bash, `min: 1` |
| A written block diff carries the `**Decisions:**` list with a `Question:`, a `Choice:` and a `Reasoning:` | `selection-logged-as-a-decision` | `tool_used` Write, `min: 1` |
| A written block diff sets `**Outcome:** slice-chosen` on the block | `outcome-set-on-the-block` | `tool_used` Write, `min: 1` |
| No `/craft:plan task/...` command line is printed | `no-plan-command-printed` | `regex`, `not_contains`, over the final message |

## Limitations

- The block is graded from the `lore record update --diff` commands the run issued (`tool_used` scans every call), not from the created record: `regex` and `llm` with `target: files` / `focus: files` see only the names of files created through the CLI, never their contents, and the record's name is the agent's choice, so `source: file` cannot name it. A `tool_used` match shows the command was issued, not that it applied; a block write that failed would end `stuck`, and the final-line grader would fail.

- `block-open-command-issued` shows that a block diff was written to a file with the file-writing tool, not that it landed on the new record.
- One spec, one phrasing, one model tier, three runs.
- "Asked nothing" is behavioural and judged by a model over the first and last 12 events of the trace.
- The session cannot answer, so a ritual that asked and stopped would end without the outcome line and fail the second clause.

## Addendum: no-decisions grader

Authored with the follow-ups entries-path case, before any run of it. `reports-no-found-work-decisions` is a `regex` over the final message for `The slice made no decisions about found work.`: this fixture has no `## Follow-ups` section, so the pass makes no found-work decision and slice's outcome prints the line `found_work.py report --session slice` prints. Expected red on the unedited prose (nothing prints it) and green on the edited prose.

Correction, after the first edited-prose run: that run printed no no-decisions line because `found_work.py` could not run in this fixture (`session-unreadable`, `spec-unreadable`): the scaffold had no `bin/lore` shim on `PATH` and no seeded session, and the prompt passed no session id. The scaffold now writes the shim, `fixtures/session.json` seeds the session `aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee`, and the prompt passes that id to the scripts that take one and puts `bin` first on `PATH`, as `slice-folds-entries-that-touch-its-code` does. This is an authoring error in the fixture, not a prose result; the baseline is re-run on the corrected fixture.
