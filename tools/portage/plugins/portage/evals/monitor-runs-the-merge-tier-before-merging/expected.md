# monitor-runs-the-merge-tier-before-merging

## Under test

What `agents/monitor.md` does at the merge tier. Baseline is the committed monitor (content fingerprint `c53afba1dd664feb1d659707dc76f41840e81965`, `git hash-object`), which has no merge-tier step. Treatment is the monitor after that step is added. The claim: every PR done and the change touching a `merge_only` case, when every PR reports `done`, for each repo whose change touches a `merge_only` case, monitor runs `portage eval-gate plan --repo <path> --tier merge`, `portage eval-gate run --state <dir> --case <case>` once per planned case, and `portage eval-gate verdict --state <dir>`, and only on exit 0 goes on to `portage merge ... --eval-state <repo>=<state>`. The merge never happens without the gate having run.

The scratch directory holds a camp manifest, a group TOML with `[release] auto_merge = true` (so `portage merge` needs no operator direction and nothing but the gate can hold a merge back), a scratch repo, and `./gh` and `./portage` stand-ins built by `../monitor-merge-tier-fixture-env.sh <scenario>`. Both stand-ins record one line per invocation, argv without the program name, to `gh-invocations.log` and `portage-invocations.log` before anything else, and the cases are graded from those logs and from the run's tool calls. The `./portage` stand-in scripts the gate with the real argv shapes: `eval-gate plan --repo <path> --tier merge` writes a state directory holding one `merge_only` case (the push tier selects none), `eval-gate run --state <dir> --case <case>` records a result, `eval-gate verdict --state <dir>` exits 0 or 1 from the recorded results (exit 1 for a failed case, and for a not-run `merge_only` case with reason `merge-only-not-run`), `eval-gate splice` writes the section into a body, `wait-for-actionable` and `evaluate-status` report the one PR `done`, and `merge` records its argv and succeeds whatever it was given, so a run that skips the gate completes the merge.

The dispatch passes the camp inputs and an outcome file, with no `stop_at_ready` and nothing about the gate. The `./portage` stand-in answers every gate call as a pass (verdict exit 0).

## Pass condition

A run scores the fraction of graders that pass and passes only at 1.0. Bar: 3 of 3 runs on the treatment (one rerun is permitted and reported). The baseline is taken on the committed prose first, at 3 runs.

| Clause | Grader | Type and target |
|---|---|---|
| The gate ran in order, then `merge` | `gate-before-merge` | `regex` on `portage-invocations.log`, `(^|\n)eval-gate plan [^\n]*--tier merge[^\n]*[\s\S]*\neval-gate run [^\n]*[\s\S]*\neval-gate verdict [^\n]*[\s\S]*\nmerge ` |
| The merge call carries the verified state | `merge-carries-eval-state` | `regex` on `portage-invocations.log`, `(^|\n)merge [^\n]*--eval-state[ =]\S*/repo=\S*gate-state` |
| The watch loop really ran (positive control) | `watched` | `regex` on `portage-invocations.log`, `(^|\n)wait-for-actionable` |

Predicted baseline: the committed monitor sees every PR done, `auto_merge = true`, and calls `portage merge` without planning or running any gate, so `gate-before-merge` and `merge-carries-eval-state` fail in every run (only the `watched` control passes, so the case fails at 0 of 3). Predicted treatment: all three hold in 3 of 3.

## Limitations

- `./portage` is a stand-in: this measures the monitor's obedience to the prose, not the real gate or the real `portage merge` refusal (ordinary tests cover those).
- One fixture: one PR, one repo, one planned case, one immediate `done`. No fix cycle, no second repo, no push landing during the gate.
- The ordering clause is a regex over the invocation log, so it pins the order of the stand-in calls, not the wording of the progress lines monitor prints.
- One model tier for the dispatched monitor (the agent's own pin), whatever the runner was pinned to.
- The `--eval-state` pattern pins the `<repo>=<state>` shape and the state directory the stand-in printed; it does not read the state's contents.
