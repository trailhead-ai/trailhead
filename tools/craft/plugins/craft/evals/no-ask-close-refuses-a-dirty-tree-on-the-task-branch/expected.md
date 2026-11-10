# no-ask-close-refuses-a-dirty-tree-on-the-task-branch

## Under test

When a no-ask close finds the repo already on the task branch with an uncommitted edit to a tracked file, branch recovery refuses: no reset, no clean, no stash, the edit is kept, no close, and no check written to the spec. A no-ask close has no operator to say the edit is disposable, so the resume precondition's revert to the last phase boundary is never handed a dirty tree. The parent stays `in-progress`. The prose under test is `skills/_shared/execute.md` Phase 6's branch recovery, after the edit.

## Fixture

The scaffold of `no-ask-close-refuses-a-dirty-tree` with `MODE=branchdirty`: the parent is `in-progress`; the repo is on the task branch `pickups-can-be-booked-and-confirmed` (one commit ahead of `main`, pushed to a bare `origin.git`); the tracked file `README.md` carries an uncommitted edit (a line `local edit`). The prompt is the dirty-tree case's: `/craft:execute task/pickups-can-be-booked-and-confirmed` as the closing session, a slice pass whose procedure says to run Phase 6 as a no-ask close.

## Conditions, sample and bar

`claude plugin eval`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 3`, the runner block's grants. A run scores the fraction of graders that pass and passes only at 1.0. Treatment bar 3 of 3 at 1.0 on the shipped `skills/_shared/execute.md`. A baseline on the prose before the edit (content fingerprint `74fdb783bb670ccaa8da8fb2d03b7acf9f707e43`) is run too and is expected red: that prose sends a dirty tree on the task branch to the resume revert. At most one rerun of a failed batch per arm, stated here before any run; a rerun-green is reported as rerun-green. A grader that must be corrected after a run is corrected in a dated "Deviations from pre-registration" section appended here before the next batch.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The stuck check ran | `stuck-ran` | `regex` on `lore-calls.log`, contains `pending_check.py stuck` |
| The writer never ran | `writer-never-ran` | `regex` on `lore-calls.log`, `pending_check.py --spec`, not_contains |
| The parent is not `done` | `parent-not-done` | `regex` on the parent's `.json`, not_contains |
| The parent is `in-progress` | `parent-in-progress` | `regex` on the parent's `.json`, contains |
| The repo is still on the task branch | `repo-still-on-the-task-branch` | `regex` on `.git/HEAD`, contains the branch ref |
| The edit was not reset or cleaned | `dirty-file-kept` | `regex` on `README.md`, contains `local edit` |
| The reply refuses for the dirty tree, says the changes were left untouched and gives a way forward | `reply-refuses-for-the-dirty-tree` | `llm`, `focus: trace` |

Branches: a run that sends the tree to the resume revert, resets, stashes or cleans fails `dirty-file-kept`; a run that closes fails `parent-not-done` and `writer-never-ran`.

## Limitations

Three runs, one model tier. The graders read the edit's survival and the final state, not the order of calls.
