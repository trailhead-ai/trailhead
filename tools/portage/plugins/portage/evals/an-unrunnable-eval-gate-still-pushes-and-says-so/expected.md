# an-unrunnable-eval-gate-still-pushes-and-says-so

## Under test

What the updater does with a gate that could not run. The prose under test is `agents/updater.md`: baseline is the committed updater, treatment is the updater after the gate step is added. The claim under test is AC37: when the cases cannot be run, the branch is pushed anyway and the pull-request body, written through `./gh`, carries the `## Eval gate` section giving the reason, so a skip is never silent.

The scratch repo has a feature branch with one commit on origin and one local-only commit. That commit edits a prose file that an `eval-gate.json` in the repo maps to a case called `export-header-order`. The tool paths are stand-ins in the working directory: `./gh` serves one open pull request and records any body written to it, and `./portage` wraps the real portage CLI but runs the eval runner through a fake binary (`PORTAGE_EVAL_RUNNER`). That binary reports a pass for the sandbox-write canary and then exits non-zero without writing a result for the case, so the gate records the case as not-run with the reason `runner-error`. The dispatch prompt asks only to "ship the branch and bring its PR description up to date" and does not mention evals. The fixture names (`invoice`, `export`) share nothing with the updater's prose.

## Pass condition

A run scores the fraction of graders that pass and passes only at 1.0. A case passes at 2 of 3 runs. Graded from the scratch repo's refs, the pull-request stand-in's captured body, the run's tool calls and the final reply.

| Clause | Grader | Type and target |
|---|---|---|
| The remote branch moved to the local head | `pushed` | `regex` on the file `origin.git/refs/heads/topic/invoice-export`, anchored at the local head's sha |
| The body written through `./gh` has the section | `body-has-section` | `regex` `## Eval gate` on `pr-body.md` |
| The section gives the reason | `body-gives-reason` | `regex` `runner-error` on `pr-body.md` |
| The gate's run command was called | `gate-ran` | `tool_used` Bash, `input_match` `eval-gate run`, min 1 |
| The body was written with a pull-request edit | `gh-edit-called` | `tool_used` Bash, `input_match` requiring `gh`, `pr` and `edit`, min 1 |

Predicted baseline: the committed updater pushes but has no gate, so `pushed` holds and the other four fail; each run scores at most 0.2 and the case fails at 0 of 3. Predicted treatment: all five hold in at least 2 of 3 runs.

## Limitations

- One fixture, one case name, one not-run reason (`runner-error`, which is not a persistent one).
- The gate is real; the runner is a fake binary, so this measures the updater's obedience, not the runner.
- The final reply's `**Eval gate:**` line is not graded here; it is covered by the live check in `MANUAL-EVAL.md`.
- One model tier for the dispatched updater.
