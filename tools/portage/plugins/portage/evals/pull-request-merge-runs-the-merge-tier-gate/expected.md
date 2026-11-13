# pull-request-merge-runs-the-merge-tier-gate

## Under test

What the `merge` verb of `skills/pull_request/SKILL.md` does at the merge tier. Baseline is the committed skill (content fingerprint `04a673f92c92f361ded894abfe0279c96f41841c`, `git hash-object`), whose `merge` verb goes straight to `portage merge`. Treatment is the skill after the verb gains the gate. The claim: `/portage:pull_request merge <pairs>`, for a repo whose change touches a `merge_only` case, runs `portage eval-gate plan --repo <path> --tier merge`, `run` once per planned case and `verdict` before `portage merge`, and passes `--eval-state <repo>=<state>` to it.

The scratch directory holds a camp manifest, a group TOML with `[release] auto_merge = true` (so `portage merge` needs no operator direction and nothing but the gate can hold a merge back), a scratch repo, and `./gh` and `./portage` stand-ins built by `../monitor-merge-tier-fixture-env.sh <scenario>`. Both stand-ins record one line per invocation, argv without the program name, to `gh-invocations.log` and `portage-invocations.log` before anything else, and the cases are graded from those logs and from the run's tool calls. The `./portage` stand-in scripts the gate with the real argv shapes: `eval-gate plan --repo <path> --tier merge` writes a state directory holding one `merge_only` case (the push tier selects none), `eval-gate run --state <dir> --case <case>` records a result, `eval-gate verdict --state <dir>` exits 0 or 1 from the recorded results (exit 1 for a failed case, and for a not-run `merge_only` case with reason `merge-only-not-run`), `eval-gate splice` writes the section into a body, `wait-for-actionable` and `evaluate-status` report the one PR `done`, and `merge` records its argv and succeeds whatever it was given, so a run that skips the gate completes the merge.

The dispatch is the slash command with one `<repo_path>:<pr>:<member>` pair, the manifest and the group TOML. The `./portage` stand-in answers every gate call as a pass (verdict exit 0). No `monitor` agent is involved.

## Pass condition

A run scores the fraction of graders that pass and passes only at 1.0. Bar: 3 of 3 runs on the treatment (one rerun is permitted and reported). The baseline is taken on the committed prose first, at 3 runs.

| Clause | Grader | Type and target |
|---|---|---|
| The gate ran in order, then `merge` | `gate-before-merge` | `regex` on `portage-invocations.log`, `(^|\n)eval-gate plan [^\n]*--tier merge[^\n]*[\s\S]*\neval-gate run [^\n]*[\s\S]*\neval-gate verdict [^\n]*[\s\S]*\nmerge ` |
| The merge call carries the verified state | `merge-carries-eval-state` | `regex` on `portage-invocations.log`, `(^|\n)merge [^\n]*--eval-state[ =]\S*/repo=\S*gate-state` |

Predicted baseline: the committed skill's `merge` verb runs `portage merge` with the pairs and no gate, so both graders fail in every run and the case fails at 0 of 3. Predicted treatment: both hold in 3 of 3.

## Limitations

- `./portage` is a stand-in: this measures the monitor's obedience to the prose, not the real gate or the real `portage merge` refusal (ordinary tests cover those).
- One fixture: one PR, one repo, one planned case, one immediate `done`. No fix cycle, no second repo, no push landing during the gate.
- The ordering clause is a regex over the invocation log, so it pins the order of the stand-in calls, not the wording of the progress lines monitor prints.
- One model tier for the dispatched monitor (the agent's own pin), whatever the runner was pinned to.
- Only the pass path is covered for the verb; the blocked outcomes belong to the monitor cases, because the verb has no outcome file.
