# no-ask-close-switches-to-the-task-branch

## Under test

A no-ask close ("run Phase 6 as a no-ask close") runs branch recovery before the push step: it reads `craft/branch` from the parent's sidecar, validates it, finds the repo holding that branch, requires a clean working tree there, switches to the branch, and confirms the repo is on it before pushing and closing. The prose under test is `skills/_shared/execute.md` Phase 6's branch recovery, after the edit (the unedited prose has none, so the baseline is the sibling case `close-asks-nothing-when-no-one-is-there`'s arm 1; this case's own baseline is not run because that prose never switches branches and the result would be uninformative about the question asked).

## Fixture

The same scaffold as `close-asks-nothing-when-no-one-is-there` (same spec, same slice parent covering AC1 to AC3, same stand-ins), except the run directory's repository is checked out on `main`, clean, while the task branch `pickups-can-be-booked-and-confirmed` exists locally and on the bare `origin.git`, with nothing to push on it.

## Conditions, sample and bar

`claude plugin eval`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, the runner block's grants (plus any this case adds, named in the README), edited prose. Bar 3 of 3 at 1.0; one rerun of a failed batch permitted, reported as rerun-green. If a git repository with a bare origin cannot be stood up in the runner's sandbox this arm is reported as not run, not faked.

## Pass condition

| Clause | Grader | Type and target |
|---|---|---|
| The repo is on the task branch at the end | `repo-on-task-branch` | `regex` on `.git/HEAD`, contains `refs/heads/pickups-can-be-booked-and-confirmed` |
| The writer ran with entries | `writer-ran` | `regex` on `lore-calls.log` |
| The parent is `done` | `parent-is-done` | `regex` on the parent's `.json` |
| No attestation written | `no-attestation-written` | `regex` on the parent's body, not_contains |
| The reply points to the check list | `hands-off-to-checks` | `regex` over the final message |

Branches: today's prose stays on `main`, so `repo-on-task-branch` fails; a run that closes without switching fails the same clause; a run that switches and closes passes all. A run that resets or stashes instead of switching still passes this case (the tree is clean here); the dirty-tree case pins that.

## Limitations

Three runs, one model tier, one phrasing. Ordering against the push step is not observable; only the final `HEAD`.

## Revision (2026-11-10, entry route): the case enters through a real caller

Pre-registered before the batch. The prose under test says the phrase "run Phase 6 as a no-ask close" counts only in a calling skill's own procedure text, and that a run started with `/craft:execute` never enters the no-ask close. This case's prompt ran `/craft:execute task/...` with the phrase in the user prompt, so its passing runs showed the rule was not being obeyed. The prompt now runs `/craft:drive spec/schedule-courier-pickups` and places the session inside the driver's step 9, the build phase (steps 1 to 8 done), whose own procedure text carries the phrase; it is bounded to stop once step 9 has finished and to say how it ended, with nothing dispatched for the PR tail. This is the entry route `driver-ends-a-slice-without-asking-about-its-check` already uses. Chosen over drive's step-4 stuck check and slice's step-5 stuck check because it needs no fixture change (the scaffold is untouched; step 4's detector would need a child task on the parent and slice's step 5 needs the slice scripts and ledger the scaffold does not carry), and every grader reads the call log, the vault or the repo, which the route leaves as before. The prompt no longer contains the phrase. Graders: unchanged. Bar 3 of 3 at 1.0 on the shipped prose (fingerprints recorded in `tools/craft/MANUAL-EVAL.md`); at most one rerun. No baseline is run for the re-route.
