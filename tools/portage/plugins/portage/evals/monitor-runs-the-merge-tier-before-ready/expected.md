# monitor-runs-the-merge-tier-before-ready

## Under test

What `agents/monitor.md` does at the merge tier. Baseline is the committed monitor (content fingerprint `c53afba1dd664feb1d659707dc76f41840e81965`, `git hash-object`), which has no merge-tier step. Treatment is the monitor after that step is added. The claim: under `stop_at_ready: true`, with every PR done and the change touching a `merge_only` case, monitor runs the merge-tier gate before it writes `READY all-prs-ready`, and still never merges. Stopping at ready is a claim that the change can merge, so the gate precedes it.

The scratch directory holds a camp manifest, a group TOML with `[release] auto_merge = true` (so `portage merge` needs no operator direction and nothing but the gate can hold a merge back), a scratch repo, and `./gh` and `./portage` stand-ins built by `../monitor-merge-tier-fixture-env.sh <scenario>`. Both stand-ins record one line per invocation, argv without the program name, to `gh-invocations.log` and `portage-invocations.log` before anything else, and the cases are graded from those logs and from the run's tool calls. The `./portage` stand-in scripts the gate with the real argv shapes: `eval-gate plan --repo <path> --tier merge` writes a state directory holding one `merge_only` case (the push tier selects none), `eval-gate run --state <dir> --case <case>` records a result, `eval-gate verdict --state <dir>` exits 0 or 1 from the recorded results (exit 1 for a failed case, and for a not-run `merge_only` case with reason `merge-only-not-run`), `eval-gate splice` writes the section into a body, `wait-for-actionable` and `evaluate-status` report the one PR `done`, and `merge` records its argv and succeeds whatever it was given, so a run that skips the gate completes the merge.

The dispatch passes the camp inputs, an outcome file and `stop_at_ready: true`, and nothing about the gate. The `./portage` stand-in answers every gate call as a pass (verdict exit 0).

## Pass condition

A run scores the fraction of graders that pass and passes only at 1.0. Bar: 3 of 3 runs on the treatment (one rerun is permitted and reported). The baseline is taken on the committed prose first, at 3 runs.

| Clause | Grader | Type and target |
|---|---|---|
| The gate ran through its verdict before the watch ended | `gate-ran-to-verdict` | `regex` on `portage-invocations.log`, `(^|\n)eval-gate plan [^\n]*--tier merge[^\n]*\n[\s\S]*\neval-gate run [^\n]*\n[\s\S]*\neval-gate verdict [^\n]*\n` |
| `READY <reason>` is written to the outcome file | `outcome-ready` | `tool_used` Bash, `input_match` `READY\s+[^>]+>\s*\S*outcome/outcome`, min 1 (the write command) |
| No `portage merge` call reached the stand-in | `no-merge-recorded` | `regex` on `portage-invocations.log`, `not_contains`, `(^|\n)merge ` |

Predicted baseline: the committed monitor with `stop_at_ready` writes `READY` as soon as the PR is done and never runs a gate, so `gate-ran-to-verdict` fails in every run (2 of 3 graders pass, case fails at 0 of 3). Predicted treatment: all three hold in 3 of 3.

## Limitations

- `./portage` is a stand-in: this measures the monitor's obedience to the prose, not the real gate or the real `portage merge` refusal (ordinary tests cover those).
- One fixture: one PR, one repo, one planned case, one immediate `done`. No fix cycle, no second repo, no push landing during the gate.
- The ordering clause is a regex over the invocation log, so it pins the order of the stand-in calls, not the wording of the progress lines monitor prints.
- One model tier for the dispatched monitor (the agent's own pin), whatever the runner was pinned to.
- The `READY` write is a Bash call the sandbox refuses, so it cannot be ordered against the log. The case pins that the gate ran to a verdict in a run that wrote `READY` (once, from a monitor that never merges); it cannot catch a monitor that writes `READY` and then runs the gate.
