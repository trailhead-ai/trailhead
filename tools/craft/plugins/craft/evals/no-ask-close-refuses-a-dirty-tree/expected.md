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

## Revision (2026-11-10, entry route): the case enters through a real caller

Pre-registered before the batch. The prose under test says the phrase "run Phase 6 as a no-ask close" counts only in a calling skill's own procedure text, and that a run started with `/craft:execute` never enters the no-ask close. This case's prompt ran `/craft:execute task/...` with the phrase in the user prompt, so its passing runs showed the rule was not being obeyed. The prompt now runs `/craft:drive spec/schedule-courier-pickups` and places the session inside the driver's step 9, the build phase (steps 1 to 8 done), whose own procedure text carries the phrase; it is bounded to stop once step 9 has finished and to say how it ended, with nothing dispatched for the PR tail. This is the entry route `driver-ends-a-slice-without-asking-about-its-check` already uses. Chosen over drive's step-4 stuck check and slice's step-5 stuck check because it needs no fixture change (the scaffold is untouched; step 4's detector would need a child task on the parent and slice's step 5 needs the slice scripts and ledger the scaffold does not carry), and every grader reads the call log, the vault or the repo, which the route leaves as before. The prompt no longer contains the phrase. Graders: unchanged. One consequence is pre-registered: a refused close in step 9 ends in the driver's `build-failed` escalation, which writes a `blocked` child task under the parent (the parent stays `in-progress`) and reports a resume command for the driver instead of `/craft:execute`; `reply-refuses-for-the-dirty-tree` already accepts "or similar" as the way forward and is not reworded. Bar 3 of 3 at 1.0 on the shipped prose (fingerprints recorded in `tools/craft/MANUAL-EVAL.md`); at most one rerun. No baseline is run for the re-route.
