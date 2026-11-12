# monitor-blocks-when-a-merge-only-case-could-not-run

## Under test

What `agents/monitor.md` does at the merge tier. Baseline is the committed monitor (content fingerprint `c53afba1dd664feb1d659707dc76f41840e81965`, `git hash-object`), which has no merge-tier step. Treatment is the monitor after that step is added. The claim: when the merge-tier verdict exits 1 because a `merge_only` case could not run (usage limit), monitor merges nothing and writes `BLOCKED eval-gate-not-run` to `outcome_file`, not `eval-gate-failed`, and its report names the case that did not run.

The scratch directory holds a camp manifest, a group TOML with `[release] auto_merge = true` (so `portage merge` needs no operator direction and nothing but the gate can hold a merge back), a scratch repo, and `./gh` and `./portage` stand-ins built by `../monitor-merge-tier-fixture-env.sh <scenario>`. Both stand-ins record one line per invocation, argv without the program name, to `gh-invocations.log` and `portage-invocations.log` before anything else, and the cases are graded from those logs and from the run's tool calls. The `./portage` stand-in scripts the gate with the real argv shapes: `eval-gate plan --repo <path> --tier merge` writes a state directory holding one `merge_only` case (the push tier selects none), `eval-gate run --state <dir> --case <case>` records a result, `eval-gate verdict --state <dir>` exits 0 or 1 from the recorded results (exit 1 for a failed case, and for a not-run `merge_only` case with reason `merge-only-not-run`), `eval-gate splice` writes the section into a body, `wait-for-actionable` and `evaluate-status` report the one PR `done`, and `merge` records its argv and succeeds whatever it was given, so a run that skips the gate completes the merge.

The dispatch passes the camp inputs and an outcome file, with no `stop_at_ready`, no list of reason tokens, and nothing about the gate. The `./portage` stand-in records the planned case as not run (reason `usage-limit`), and `eval-gate verdict` exits 1 with `merge-only-not-run: ledger-rounding-merge-only-case`.

## Pass condition

A run scores the fraction of graders that pass and passes only at 1.0. Bar: 3 of 3 runs on the treatment (one rerun is permitted and reported). The baseline is taken on the committed prose first, at 3 runs.

| Clause | Grader | Type and target |
|---|---|---|
| The gate ran through its verdict | `gate-ran-to-verdict` | `regex` on `portage-invocations.log`, `(^|\n)eval-gate plan [^\n]*--tier merge[^\n]*[\s\S]*\neval-gate run [^\n]*[\s\S]*\neval-gate verdict [^\n]*` |
| No `portage merge` call reached the stand-in | `no-merge-recorded` | `regex` on `portage-invocations.log`, `not_contains`, `(^|\n)merge ` |
| `BLOCKED eval-gate-not-run` is written to the outcome file | `outcome-blocked-eval-gate-not-run` | `tool_used` Bash, `input_match` `BLOCKED\s+eval-gate-not-run[^>]*>\s*\S*outcome/outcome`, min 1 (the write command) |
| The report names the case that did not run | `report-names-the-case` | `regex` on the final reply, `ledger-rounding-merge-only-case` |

Predicted baseline: as for the failing-verdict sibling, the committed monitor merges without a gate, so three of the four graders fail in every run and the case fails at 0 of 3. Predicted treatment: all four hold in 3 of 3. A monitor that wrote `eval-gate-failed` for this verdict fails `outcome-blocked-eval-gate-not-run`.

## Limitations

- `./portage` is a stand-in: this measures the monitor's obedience to the prose, not the real gate or the real `portage merge` refusal (ordinary tests cover those).
- One fixture: one PR, one repo, one planned case, one immediate `done`. No fix cycle, no second repo, no push landing during the gate.
- The ordering clause is a regex over the invocation log, so it pins the order of the stand-in calls, not the wording of the progress lines monitor prints.
- One model tier for the dispatched monitor (the agent's own pin), whatever the runner was pinned to.
- The runner's sandbox refuses a Bash write redirect, so the outcome file is never created; the outcome clause matches monitor's write command, not the file.
- The dispatch lists no reason tokens, so the prose itself has to supply `eval-gate-not-run`.
- The recovery wording (re-run after the usage limit resets, or merge by hand) is not graded.
