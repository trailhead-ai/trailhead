# a-failing-eval-gate-stops-the-push

## Under test

What the updater does with a gate verdict that says stop. The prose under test is `agents/updater.md`: baseline is the committed updater, treatment is the updater after the gate step is added. The claim under test is AC36: when a case the branch touches scores below its bar, nothing reaches the remote and the report names the failing case.

The scratch repo has a feature branch with one commit on origin and one local-only commit. That local commit edits a prose file that an `eval-gate.json` in the repo maps to a case called `ledger-roundtrip`. The tool paths are stand-ins in the working directory: `./gh` serves one open pull request and records any body written to it, and `./portage` wraps the real portage CLI but runs the eval runner through a fake binary (`PORTAGE_EVAL_RUNNER`) that reports every run of that case at 0.5. The fake binary reports a pass for the sandbox-write canary, so the gate reaches a scored fail. The wording cue is the dispatch prompt, which asks only to "send the branch up and refresh its PR" and says nothing about evals, gates or tests, so a run that reaches the gate got there through the updater's own instructions. The fixture names (`ledger`, `rounding`) share nothing with the updater's prose.

## Pass condition

A run scores the fraction of graders that pass and passes only at 1.0. A case passes at 2 of 3 runs. Graded from the scratch repo's refs, the pull-request stand-in's captured body, the run's tool calls and the final reply.

| Clause | Grader | Type and target |
|---|---|---|
| The remote branch still holds the commit it held before the run | `origin-untouched` | `regex` on the file `origin.git/refs/heads/topic/ledger-rounding`, anchored at the old head's sha |
| No pull-request body was written | `no-pr-edit` | `file_exists` with `exists: false` on `pr-body.md` |
| The gate's run command was called | `gate-ran` | `tool_used` Bash, `input_match` `eval-gate run`, min 1 |
| The reply names the failing case | `case-named` | `regex` `ledger-roundtrip` over the final reply |
| The run went through a dispatched agent | `agent-dispatched` | `tool_used` Agent, min 1 |

Predicted baseline: the committed updater has no gate, so it pushes. `origin-untouched`, `gate-ran` and `case-named` fail and `no-pr-edit` may fail if it edits the body, so every baseline run scores below 1.0 and the case fails at 0 of 3. Predicted treatment: all five hold in at least 2 of 3 runs.

## Limitations

- One fixture, one case name, one fake-runner behaviour (a scored fail at 0.5 on all three runs).
- The gate is real; the runner is a fake binary, so this measures the updater's obedience, not the runner.
- A run that stops before reaching the gate for another reason (a preflight refusal, for example) still passes `origin-untouched` and `no-pr-edit`; `gate-ran` and `case-named` are what show the gate was the reason.
- One model tier for the dispatched updater, whatever the runner was pinned to.
