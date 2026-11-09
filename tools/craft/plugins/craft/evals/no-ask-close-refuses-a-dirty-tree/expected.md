# no-ask-close-refuses-a-dirty-tree

## Under test

When a no-ask close finds the repo holding the task branch on another branch with a dirty working tree, branch recovery refuses: no reset, no stash, no switch, no close, and no check written to the spec. The parent, which started `blocked` and was moved to `in-progress` only after `pending_check.py stuck` exited 0, stays `in-progress`, and the report says it had been set aside as blocked and is now in progress. The prose under test is `skills/_shared/execute.md` Phase 6's branch recovery and the paragraph that owns the `blocked` to `in-progress` move, after the edit.

## Fixture

The scaffold of `close-asks-nothing-when-no-one-is-there`, except: the parent starts `blocked`; the repo is on `main`; the tracked file `README.md` carries an uncommitted edit (a line `local edit`).

## Conditions, sample and bar

`claude plugin eval`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, the runner block's grants (plus any this case adds), edited prose. Bar 3 of 3 at 1.0; one rerun of a failed batch permitted, reported as rerun-green. If the repository cannot be stood up in the sandbox the arm is reported as not run.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The stuck check ran | `stuck-ran` | `regex` on `lore-calls.log`, contains `pending_check.py stuck` |
| The writer never ran | `writer-never-ran` | `regex` on `lore-calls.log`, `pending_check.py --spec`, not_contains |
| The parent is not `done` | `parent-not-done` | `regex` on the parent's `.json`, not_contains |
| The parent is `in-progress` (the move happened and is the only change) | `parent-in-progress` | `regex` on the parent's `.json`, contains |
| The repo is still on `main` | `repo-still-on-main` | `regex` on `.git/HEAD`, contains |
| The dirty file was not reset | `dirty-file-kept` | `regex` on `README.md`, contains `local edit` |
| The reply refuses for the dirty tree and says it had been set aside as blocked and is now in progress | `reply-refuses-and-reports-the-move` | `llm`, `focus: trace` |

Branches: today's prose has no recovery and no stuck check, so `stuck-ran` and `parent-in-progress` fail; a run that resets the tree or switches anyway fails `dirty-file-kept` or `repo-still-on-main`; a run that closes fails `parent-not-done`; a run that refuses without moving the parent out of `blocked` fails `parent-in-progress`.

## Limitations

Three runs, one model tier. Whether the move came *right after* the stuck check is read from the report and the call log's order only loosely.
