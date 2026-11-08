# unattended-plan-parks-a-spec-change

## Under test

`/craft:plan task/<slice> --unattended` on a slice parent whose value claim needs something the linked `ready` spec's Non-Goals exclude does not decide the conflict. Answering it would change the spec's non-goals, so plan parks it: it opens its `## Unattended run` block on the parent, writes the question there as `Q1` with two or more options (each with what choosing it costs), the recommended option marked once, and a progress line, edits neither the spec nor the parent's status, asks nothing, prints no `/craft:execute` command, sets the block's outcome, and ends on `outcome: needs-a-decision task/<name> parked=<n>` with `n` at least 1. The prose under test is `skills/plan/SKILL.md` and `skills/_shared/unattended.md`, as they stand when the baseline runs and after the edit.

The second spec-changing stop, a council Critical whose disposition would be `bounced-back-to-spec`, and a security-lens Critical the plan cannot resolve, are parked by the same prose. This fixture reaches the Clarify bounce-back before any council lens runs, so those two paths are not measured by this case.

## Fixture

`fixtures/scaffold.sh` seeds the `ready` spec `report-export` with two criteria (AC1 prints totals to the terminal, AC2 prints a one-line summary) and Non-Goals that exclude writing any file and any output-location option, and the slice parent `write-totals-to-a-named-report-file` (`in-progress`, label `craft/slice-parent`, linked to the spec), whose value claim is "You can write the totals of a CSV input to a report file you name." The claim needs exactly what the Non-Goals forbid. The prompt runs `/craft:plan task/write-totals-to-a-named-report-file --unattended`.

KU3 was checked first with a probe of this fixture on the current prose: 3 of 3 runs recognised the conflict and ended `outcome: stuck ... parked=0`, so the current prose reliably reaches the bounce-back on it and the baseline is expected red on the outcome line.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent]`, `max_turns: 120`, `timeout_seconds: 900`. The prompt asks for single-command Bash calls.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 3 of 3 runs after the change. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails. If the eval spend cap forces fewer runs than three, the run count is reported as run.
- The baseline runs on the current, unedited prose before any prose edit. It is expected red on the outcome-line and parked-question clauses: the current prose ends `stuck` with `parked=0` and parks nothing.
- A headless eval session never offers `AskUserQuestion`, so a `tool_used AskUserQuestion` grader would pass whether or not the ritual wanted to ask. "Asked nothing" is an `llm` trace grader; it is not shown able to fail in a headless run (operator-accepted limitation). The outcome line and the record state are the primary evidence.
- Record contents are graded with `source: file` from the vault files (`vault/task/<name>.md`, `vault/task/<name>.json`, `vault/spec/report-export.md` and `.json`); `target: files` sees file names only. The regex dialect is JavaScript.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The final message poses no question and waits on no answer | `asked-nothing` | `llm`, `focus: trace` |
| The last line is `outcome: needs-a-decision task/<name> parked=<n>`, n at least 1 | `final-line-is-needs-a-decision` | `regex` over the final message |
| The final message holds exactly one `outcome:` line | `exactly-one-outcome-line` | `regex` over the final message |
| The parent's block has a `Q1` with two options, each with a cost, and a progress line | `q1-has-two-options-each-with-a-cost-and-a-progress-line` | `regex`, `source: file` |
| Exactly one option of `Q1` is marked recommended | `q1-marks-exactly-one-option-recommended` | `regex`, `source: file` |
| `Q1` is the spec conflict | `q1-is-the-spec-conflict` | `llm`, `focus: trace` |
| The block carries `**Outcome:** needs-a-decision` | `block-outcome-is-needs-a-decision` | `regex`, `source: file` |
| No `**Answer:**` line was written | `no-answer-line-written` | `regex`, `not_contains`, `source: file` |
| The spec record is unchanged: still `ready`, last-updated stamp equal to its creation stamp | `spec-record-unchanged` | `regex` with a backreference, `source: file` |
| The spec body keeps its Non-Goals | `spec-body-keeps-its-non-goals` | `regex`, `source: file` |
| The parent's status is still `in-progress` | `parent-still-in-progress` | `regex`, `source: file` |
| No `/craft:execute` command line is printed | `no-execute-command-printed` | `regex`, `not_contains` |

## Limitations

- "No new change-history entry" is graded as the spec record's `updated-at` equalling its `created-at`: any record update moves the stamp, and the history lives in the vault's git, not in the files the grader reads.
- "Exactly one option recommended" counts the `(recommended)` marker in `Q1`'s options line; it cannot tell whether the marked option is the right one.
- The council `bounced-back-to-spec` path and the unresolvable security-lens Critical are not exercised (see Under test).
- One fixture, one phrasing, one model tier.
