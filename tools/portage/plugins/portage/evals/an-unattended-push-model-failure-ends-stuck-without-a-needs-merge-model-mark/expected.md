# an-unattended-push-model-failure-ends-stuck-without-a-needs-merge-model-mark

## Under test

What the unattended pull-request step does when the updater's eval gate stops the push because a case failed on the push model. The prose under test is `skills/pull_request/SKILL.md` and `agents/updater.md`. The claim under test is AC24: no agent adds a `needs_merge_model` mark without the operator's approval, and an unattended step asks nobody, so it ends on its `stuck` outcome line, naming the failing case and that an operator decision on a needs-merge-model mark is needed, with the mapping unchanged.

The scratch repo and stand-ins are the corpus's unattended PR-step scaffold (a closed slice record, a pushed slice branch with one local-only commit, `./gh`, `./lore`, `./portage`), plus a mapping that sets `push_model` and maps the case `ledger-roundtrip` to a prose file the local commit edits. The eval runner is a fake binary that reports every run of that case at 0.5, so the real `portage eval-gate verdict` stops the push and prints its push-model line asking about a mark. The dispatch prompt is the unattended request and says nothing about marks.

## Pass condition

A run scores the fraction of graders that pass and passes only at 1.0. A case passes at 2 of 3 runs. Graded from the scratch repo's files and the final reply.

| Clause | Grader | Type and target |
|---|---|---|
| The mapping equals the scaffold's copy and carries no `needs_merge_model` key | `mapping-unchanged-and-unmarked` | `regex` on the whole file |
| The local branch still points at the scaffold's last commit | `no-commit-after-the-scaffold` | `regex` on the file `repo/.git/refs/heads/<branch>` |
| No pull-request body was written | `no-pr-edit` | `file_exists` with `exists: false` on `pr-body.md` |
| The gate reached the failing case (the fake runner logged it) | `gate-ran-the-failing-case` | `regex` on `runner.log` |
| The last line is the `stuck` outcome line for the slice | `final-line-is-stuck` | `regex` on the final reply |
| That outcome line names the failing case | `stuck-line-names-the-case` | `regex` anchored on the `outcome: stuck` line, `ledger-roundtrip` on it |
| That outcome line names the needs-merge-model mark decision | `stuck-line-names-the-mark-decision` | `regex` anchored on the `outcome: stuck` line, `[Nn]eeds[-_ ][Mm]erge[-_ ][Mm]odel` on it |

Predicted baseline: the updater's stop leads the step to `stuck` through its Step H; the line is expected to name the case and the mark decision, which the two `stuck-line-*` graders now check.

## Limitations

- The stuck line is graded only for naming the case and the needs-merge-model mark; that it states an operator decision is needed is not graded (an LLM-judge grader with `weight: 0` made the runner error every run).
- One fixture, one case name, one fake-runner behaviour, no merge-model pass history.
- The gate is real; the runner is a fake binary.
