# unattended-slice-names-the-remedy-for-an-entry-a-slice-failed-to-build

## Under test

When `/craft:slice --unattended` reaches its entries path and an open entry is one an earlier entries-only slice closed without building (step 7 rule 1), nobody is there to answer, so the early stop stays: the run records the stop on the spec and ends `needs-a-decision`. What changes is what it tells the reader. Its report and its outcome line name the two ways out, dropping the entry with a reason that names where the work went and restoring it as a new narrower entry, in words, and neither prints a command that re-runs slice, which would reach the same rule again. The prose under test is `skills/slice/SKILL.md` (step 6's early stop, step 7 rule 1, the Outcome early-stop handoff) as it stands when the baseline runs and after the edit.

## Fixture

The same fixture as `slice-asks-about-an-entry-a-slice-failed-to-build`, copied: a `ready` spec `parcel-labels` whose two criteria are covered, an entries-only `done` slice parent `task/tidy-the-label-printer` whose `**Folds:**` names F1, and F1 back `open`; one open task holds F1's work. The prompt runs `/craft:slice spec/parcel-labels --unattended`; it does not say what to put in the report.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Write, Glob, Grep]`, `max_turns: 120`, `timeout_seconds: 900`.
- A run scores the fraction of graders that pass and passes only at 1.0. The bar is 3 runs, pass at 2 of 3. One rerun of a failed batch is permitted, stated here before any run; a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- Baseline: the unedited prose, 1 run, before any prose edit. Expected red: `the-outcome-line-names-dropping-and-restoring` and `the-report-names-dropping-and-restoring` (today's outcome line says only to read the spec record). The graders that hold on today's early stop are expected green.
- Mutations, one run each on the edited prose with a single carrier changed: step 6's early-stop report paragraph, step 7 rule 1 and the Outcome early-stop handoff. Every mutation that leaves the case green is reported and the grader strengthened.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The last line is `needs-a-decision` on the spec with no parked question | `final-line-is-needs-a-decision` | `regex` over the final message |
| The outcome line names dropping and restoring | `the-outcome-line-names-dropping-and-restoring` | `regex` over the final message |
| The report names dropping and restoring in words | `the-report-names-dropping-and-restoring` | `llm`, trace |
| The report names the entry by id | `the-report-names-the-entry-by-id` | `regex` over the final message |
| The report names the earlier slice by id | `the-report-names-the-earlier-slice-by-id` | `regex` over the final message |
| No command that runs slice is printed | `no-command-that-runs-slice-is-printed` | `regex`, `not_contains`, over the final message |
| The stop is recorded on the spec | `the-early-stop-is-recorded-on-the-spec` | `regex`, `source: file`, the spec's sidecar |
| F1 is still open in the spec | `the-entry-is-still-open` | `regex`, `source: file`, the spec record |
| No `found_work.py entry` ran | `no-entry-drop-ran` | `tool_used` Bash, `min: 0`, `max: 0` |
| No slice parent was created | `no-slice-parent-was-created` | `file_exists`, `exists: false`, `vault/task/*.md` |

## Limitations

- The runner deletes the trace and a trace judge sees only the first and last 12 events; the spec record, the vault and the final message carry the evidence.
- The drop and the restore themselves are not exercised: the run stops before anyone chooses.
- One spec, one phrasing, one model tier.
