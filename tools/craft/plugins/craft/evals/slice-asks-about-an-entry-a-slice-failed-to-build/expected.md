# slice-asks-about-an-entry-a-slice-failed-to-build

## Under test

When `/craft:slice` reaches its entries path and an open entry is one an earlier entries-only slice closed without building (step 7 rule 1), an attended run no longer takes an early stop that names a re-run of slice, which would reach the same rule again. It puts the entry to the operator through the blocking prompt in `skills/_shared/escalation.md` and ends its turn waiting for the reply, writing nothing first: what went wrong naming the entry and the earlier slice by id, what is at stake, the recommended fix first and marked, each other fix with its cost, free text always allowed. The fixes are dropping the entry with a reason that names where the work went (recommended when an existing task holds it), restoring it as a new narrower entry, and stopping the loop here. The prose under test is `skills/slice/SKILL.md` (step 6's early stop, step 7 rule 1, the Outcome early-stop handoff) and `skills/_shared/escalation.md`, as they stand when the baseline runs and after the edit.

A headless run offers no choice prompt, so the session falls to the plain-text form. The prompt shape is verified on the plain-text form only, and every prompt-shape grader below reads the last message, never a tool call.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `parcel-labels` with two criteria (AC1, AC2), a `## Slices` ledger that closes both through two `done` slice parents carrying `**Covers:**`, and a third `done` slice parent, `task/tidy-the-label-printer`, whose body carries `**Folds:** spec/parcel-labels#F1` and no `**Covers:**` line, so it is an entries-only slice. The `## Follow-ups` section holds one entry, F1 (close the manifest file the label reader opens, at `labels/printer.py`), back `open`: the slice closed without building it. The vault also holds one open task, `task/label-tool-releases-the-files-it-opens`, whose value claim is the work F1 asks for, so dropping F1 with a reason that names it is credible and recommended. A small `labels/` source tree is copied into the run directory. The prompt runs `/craft:slice spec/parcel-labels` and passes the lore wrapper's instructions; it does not say what to do with the entry, and never says to ask.

The arm `unattended-slice-names-the-remedy-for-an-entry-a-slice-failed-to-build` runs the same fixture under `--unattended`; its own `expected.md` holds its clauses.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 80`, `timeout_seconds: 900`.
- A run scores the fraction of graders that pass and passes only at 1.0. The bar is 3 runs, pass at 2 of 3. One rerun of a failed batch is permitted, stated here before any run; a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- Baseline: the unedited prose, 1 run, before any prose edit. Expected red: `no-early-stop-label-was-written` (today's prose writes `craft/slice-loop=stopped`), `the-turn-ends-waiting-for-the-reply`, `the-question-offers-drop-restore-and-stop-with-their-costs`, `the-first-listed-fix-is-the-drop-naming-the-task-that-holds-the-work`, `the-question-says-what-is-at-stake` and `the-message-does-not-tell-the-operator-to-re-run-slice` (today's early stop hands off to a re-run). The graders that hold when nothing is written are expected green.
- Mutations, one run each on the edited prose with a single carrier changed, one per carrier of the rule: step 6's early-stop report paragraph, step 7 rule 1, the Outcome early-stop handoff, and the prompt shape's `After the answer` section in `escalation.md`. Every mutation that leaves the case green is reported and the grader strengthened.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| No early-stop label was written to the spec | `no-early-stop-label-was-written` | `regex`, `not_contains`, `source: file`, the spec's sidecar |
| F1 is still open in the spec | `the-entry-is-still-open` | `regex`, `source: file`, the spec record |
| No `found_work.py entry` ran | `no-entry-drop-ran` | `tool_used` Bash, `min: 0`, `max: 0` |
| No slice parent was created | `no-slice-parent-was-created` | `file_exists`, `exists: false`, `vault/task/*.md` |
| The message names the entry by id | `the-message-names-the-entry-by-id` | `regex` over the final message |
| The message names the earlier slice by id | `the-message-names-the-earlier-slice-by-id` | `regex` over the final message |
| The message does not tell the operator to re-run slice | `the-message-does-not-tell-the-operator-to-re-run-slice` | `regex`, `not_contains`, over the final message |
| The question says what is at stake | `the-question-says-what-is-at-stake` | `llm`, trace |
| The question offers drop, restore and stop, each other fix with its cost | `the-question-offers-drop-restore-and-stop-with-their-costs` | `llm`, trace |
| The first listed fix is the drop naming the task that holds the work, marked recommended | `the-first-listed-fix-is-the-drop-naming-the-task-that-holds-the-work` | `llm`, trace |
| The turn ends waiting for the reply | `the-turn-ends-waiting-for-the-reply` | `llm`, trace |

## Limitations

- The runner deletes the trace and a trace judge sees only the first and last 12 events, so the order of steps is not observed; the spec record, the vault and the final message carry the evidence. The `llm` graders are the only ones that judge content a regex cannot; their verdicts are read against the kept trace when `--keep-temp` is used.
- Only the plain-text form is observable here. The choice-prompt form is not exercised, and neither is carrying out the answer: the case stops at the question, so the drop, the restore and the pass that continues are not observed.
- A run that stops for another reason leaves the spec untouched and passes the negative graders; the positive question-shape graders tell it apart.
- One spec, one phrasing, one model tier.
