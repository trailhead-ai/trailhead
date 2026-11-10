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
| The writer never ran | `writer-never-ran` | `regex` on `lore-calls.log`, `pending_check.py --spec`, not_contains |
| The parent is not `done` | `parent-not-done` | `regex` on the parent's `.json`, not_contains |
| The parent is `in-progress` | `parent-in-progress` | `regex` on the parent's `.json`, contains |
| The repo is still on the task branch | `repo-still-on-the-task-branch` | `regex` on `.git/HEAD`, contains the branch ref |
| The edit was not reset or cleaned | `dirty-file-kept` | `regex` on `README.md`, contains `local edit` |
| The reply refuses for the dirty tree, says the changes were left untouched and gives a way forward | `reply-refuses-for-the-dirty-tree` | `llm`, `focus: trace` |

Branches: a run that sends the tree to the resume revert, resets, stashes or cleans fails `dirty-file-kept`; a run that closes fails `parent-not-done` and `writer-never-ran`.

## Limitations

Three runs, one model tier. The graders read the edit's survival and the final state, not the order of calls.

## Deviations from pre-registration

Recorded 2026-11-10, after the first batch on the shipped `skills/_shared/execute.md` (fingerprint `bc2c994706d47999bb9bcab45375ac8c8f0d606e`) and before any rerun. The grader `stuck-ran` (the call log shows `pending_check.py stuck`) is removed. It failed in 3 of 3 runs while every other grader passed, because the other six graders showed the run refusing correctly. The stuck check is the calling skill's step (the drive and slice procedures run it before they read execute's Phase 6); execute's no-ask close no longer runs or requires it, and this prompt invokes execute directly, so the grader measured a behaviour the prose under test does not ask for. It was written for the earlier prose, whose move paragraph required the check. The correction concerns what this case's prose owns, not a result: the remaining clauses are unchanged.

## Baseline result (2026-11-10)

The baseline on the unedited prose (fingerprint `74fdb783bb670ccaa8da8fb2d03b7acf9f707e43`) scored 3 of 3 at 1.0, not the expected red. The runner grants no `git reset`, `git checkout`, `git stash` or `git clean`, so the resume revert cannot execute and the unedited prose refuses with the edit kept too. This arm is therefore a guard on the shipped prose, not a detector of the old behaviour.

## Revision (2026-11-10, entry route): the case enters through a real caller

Pre-registered before the batch. The prose under test says the phrase "run Phase 6 as a no-ask close" counts only in a calling skill's own procedure text, and that a run started with `/craft:execute` never enters the no-ask close. This case's prompt ran `/craft:execute task/...` with the phrase in the user prompt, so its passing runs showed the rule was not being obeyed. The prompt now runs `/craft:drive spec/schedule-courier-pickups` and places the session inside the driver's step 9, the build phase (steps 1 to 8 done), whose own procedure text carries the phrase; it is bounded to stop once step 9 has finished and to say how it ended, with nothing dispatched for the PR tail. This is the entry route `driver-ends-a-slice-without-asking-about-its-check` already uses. Chosen over drive's step-4 stuck check and slice's step-5 stuck check because it needs no fixture change (the scaffold is untouched; step 4's detector would need a child task on the parent and slice's step 5 needs the slice scripts and ledger the scaffold does not carry), and every grader reads the call log, the vault or the repo, which the route leaves as before. The prompt no longer contains the phrase. Graders: unchanged. One consequence is pre-registered: a refused close in step 9 ends in the driver's `build-failed` escalation, which writes a `blocked` child task under the parent (the parent stays `in-progress`) and reports a resume command for the driver instead of `/craft:execute`; `reply-refuses-for-the-dirty-tree` already accepts "or similar" as the way forward and is not reworded. Bar 3 of 3 at 1.0 on the shipped prose (fingerprints recorded in `tools/craft/MANUAL-EVAL.md`); at most one rerun. No baseline is run for the re-route.
