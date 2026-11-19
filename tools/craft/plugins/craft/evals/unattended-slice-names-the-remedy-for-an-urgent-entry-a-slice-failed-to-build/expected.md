# unattended-slice-names-the-remedy-for-an-urgent-entry-a-slice-failed-to-build

## Under test

An urgent entry an earlier entries-only slice closed without building is never chosen again, even while uncovered criteria remain. When `/craft:slice --unattended` finds a security-marked open entry on the `**Folds:**` line of a `done` entries-only slice parent, the urgent tier does not choose it: step 7's first rule takes the early stop, so the run records the stop on the spec, writes no slice, and ends `needs-a-decision` with the two ways out, dropping the entry with a reason that names where the work went or restoring it as a new entry, in words and with no command that re-runs slice. The prose under test is `skills/slice/SKILL.md` (step 7's urgent tier and first rule, step 6's early stop, the Outcome early-stop handoff) as it stands when the baseline runs and after the edit.

## Fixture

A `ready` spec `shelf-labels` with AC1 covered and AC2 uncovered, an entries-only `done` slice parent `task/tidy-the-label-printer` whose `**Folds:**` names F1, and F1 back `open`. F1 is security-marked (`security: yes` in the listing), `where` `labels/printer.py`. One open task holds F1's work. The prompt runs `/craft:slice spec/shelf-labels --unattended`; it does not say what to put in the report. Uncovered AC2 is what makes the case discriminating: a run that chooses a slice by the criteria, or by the urgent tier, folds F1 again.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Write, Glob, Grep]`, `max_turns: 120`, `timeout_seconds: 900`.
- A run scores the fraction of graders that pass and passes only at 1.0. The bar is 3 runs, pass at 2 of 3. One rerun of a failed batch is permitted, stated here before any run; a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- Baseline: the unedited prose, 1 run, before any prose edit. Expected red: the final line, the outcome line, the report's remedy, the recorded stop, no slice parent, no covers gate.
- Mutation, one run on the edited prose: the sentence that sends an urgent entry on a done entries-only parent's `**Folds:**` line to step 7's first rule.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The last line is `needs-a-decision` on the spec with no parked question | `final-line-is-needs-a-decision` | `regex` over the final message |
| The outcome line names dropping and restoring | `the-outcome-line-names-dropping-and-restoring` | `regex` over the final message |
| The report names dropping and restoring in words | `the-report-names-dropping-and-restoring` | `llm`, trace |
| The report names the entry by id | `the-report-names-the-entry-by-id` | `regex` over the final message |
| The report names the earlier slice by id | `the-report-names-the-earlier-slice-by-id` | `regex` over the final message |
| No command that runs slice is printed | `no-command-that-runs-slice-is-printed` | `regex`, `not_contains` |
| The stop is recorded on the spec | `the-early-stop-is-recorded-on-the-spec` | `regex`, `source: file`, the spec's sidecar |
| F1 is still open in the spec | `the-entry-is-still-open` | `regex`, `source: file`, the spec record |
| No `found_work.py entry` ran | `no-entry-drop-ran` | `tool_used` Bash, `max: 0` |
| The covers gate never ran | `no-covers-gate-ran` | `tool_used` Bash, `max: 0` |
| No slice parent was created | `no-slice-parent-was-created` | `file_exists`, `exists: false`, `vault/task/*.md` |

## Limitations

- The runner deletes the trace and a trace judge sees only the first and last 12 events; the spec record, the vault and the final message carry the evidence.
- `no-slice-parent-was-created` counts only files created during the run, so it sees a new parent record but not a modified one; `no-covers-gate-ran` is the second witness.
- The drop and the restore themselves are not exercised: the run stops before anyone chooses.
- One spec, one phrasing, one model tier.
