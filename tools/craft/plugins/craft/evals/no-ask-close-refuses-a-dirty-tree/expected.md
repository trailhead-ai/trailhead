# no-ask-close-refuses-a-dirty-tree

## Under test

When a no-ask close finds the repo holding the task branch on another branch with a dirty working tree, branch recovery refuses: no reset, no stash, no switch, no close, and no check written to the spec. The parent is `in-progress` and stays so. The prose under test is `skills/_shared/execute.md` Phase 6's branch recovery.

## Fixture

The scaffold of `close-asks-nothing-when-no-one-is-there`, except: the parent is `in-progress`; the repo is on `main`; the tracked file `README.md` carries an uncommitted edit (a line `local edit`).

## Conditions, sample and bar

`claude plugin eval`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, the runner block's grants (plus any this case adds), edited prose. Bar 3 of 3 at 1.0; one rerun of a failed batch permitted, reported as rerun-green. If the repository cannot be stood up in the sandbox the arm is reported as not run.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The writer never ran | `writer-never-ran` | `regex` on `lore-calls.log`, `pending_check.py --spec`, not_contains |
| The parent is not `done` | `parent-not-done` | `regex` on the parent's `.json`, not_contains |
| The parent is `in-progress` (nothing changed) | `parent-in-progress` | `regex` on the parent's `.json`, contains |
| The repo is still on `main` | `repo-still-on-main` | `regex` on `.git/HEAD`, contains |
| The dirty file was not reset | `dirty-file-kept` | `regex` on `README.md`, contains `local edit` |
| The reply refuses for the dirty tree and gives a way forward | `reply-refuses-for-the-dirty-tree` | `llm`, `focus: trace` |

Branches: today's prose has no recovery and no stuck check, so `stuck-ran` and `parent-in-progress` fail; a run that resets the tree or switches anyway fails `dirty-file-kept` or `repo-still-on-main`; a run that closes fails `parent-not-done`; a run that refuses without moving the parent out of `blocked` fails `parent-in-progress`.

## Limitations

Three runs, one model tier. Whether the move came *right after* the stuck check is read from the report and the call log's order only loosely.

## Revision (2026-11-10): no blocked move

The operator removed the `blocked` to `in-progress` move. The scaffold's parent is now `in-progress` (the `dirty` mode no longer sets `blocked`), the prompt no longer says the slice was set aside as blocked, and the grader `reply-refuses-and-reports-the-move` became `reply-refuses-for-the-dirty-tree`, which no longer asks for a move line. The other graders are unchanged. The branch recovery now refuses a dirty tree on any branch; this arm covers a dirty tree on another branch, and the new case `no-ask-close-refuses-a-dirty-tree-on-the-task-branch` covers the task branch. Bar 3 of 3 at 1.0 on the shipped prose; at most one rerun.

## Deviations from pre-registration

Recorded 2026-11-10, after the first batch on the shipped `skills/_shared/execute.md` (fingerprint `bc2c994706d47999bb9bcab45375ac8c8f0d606e`) and before any rerun. The grader `stuck-ran` (the call log shows `pending_check.py stuck`) is removed. It failed in 3 of 3 runs while every other grader passed, because the other six graders showed the run refusing correctly. The stuck check is the calling skill's step (the drive and slice procedures run it before they read execute's Phase 6); execute's no-ask close no longer runs or requires it, and this prompt invokes execute directly, so the grader measured a behaviour the prose under test does not ask for. It was written for the earlier prose, whose move paragraph required the check. The correction concerns what this case's prose owns, not a result: the remaining clauses are unchanged.
