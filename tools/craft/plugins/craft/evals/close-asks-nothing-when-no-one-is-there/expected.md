# close-asks-nothing-when-no-one-is-there

## Under test

When the caller's own procedure says "run Phase 6 as a no-ask close", Phase 6 puts no question about any check to the operator. Every covered `manual-check` criterion with no attestation (AC2) and every covered criterion with no observation whose spec trailer is manual check (AC3) is taken as "Not yet": the session drafts a `check` and `how` for each, judges `changed_behaviour`, writes the entries once through `pending_check.py` naming exactly those two criteria, runs `pending_check.py verify`, then the observation gate, then the close write. No attestation line is written. The completion report shows the drafted `check` / `how` lines, says the closing session drafted them and nobody reviewed them, and points to `/craft:checks`. The prose under test is `skills/_shared/execute.md` Phase 6 as it stands when the baseline runs and after the edit (the new no-ask discharge, the branch recovery that runs first, and the completion report's no-ask wording). The attended discharge is not under test here; its regression arm is the existing case `close-defers-an-unconfirmed-check`, run on the edited prose.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `schedule-courier-pickups` with `- **ACn.**` criteria AC1 to AC3 (AC1 automated, AC2 and AC3 `*Verified by: manual check.*`) and an `in-progress` slice parent `pickups-can-be-booked-and-confirmed` (labels `craft/slice-parent` and `craft/branch`, related to the spec) that covers AC1, AC2 and AC3. Its `## Criterion observations` carries `automated-assertion` for AC1 and `manual-check` for AC2 and **no line for AC3**; there is no `## Operator attestations`, and the `## End Phases` list is ticked through Phase 5. The vault is named `eval`.

The run directory is also a git repository (the single current repo, as vanilla usage defines the set): `main` with one commit, the task branch with one more, both pushed to a bare `origin.git` in the run directory, the task branch checked out and clean, nothing to push. Fixture files are hidden from `git status` through `.git/info/exclude`.

Stand-ins, because the runner's sandbox hides the operator's home: `./lore` is the self-contained wrapper the sibling waiting-checks cases use, logging every call's arguments to `lore-calls.log`; `./scripts/pending_check.py` and `./scripts/observation_gate.py` are thin wrappers around **real copies** of craft's scripts, run against a `bin/lore` that reaches the same seeded vault. The wrappers add a line to `lore-calls.log` per call, and the `pending_check.py` wrapper copies the `--entries` file's contents into `pending-check-entries.log` at call time. So `lore-calls.log` is the ordered log of everything the agent ran through the vault, and the writer's effects are real vault writes. The real `stuck`, `describe`, `verify` and write modes all run.

The prompt resumes the run at Phase 6 (every task done, flow-out ticked, nothing to push, no `## Enumerated states`), says the invocation is the driver's build phase whose procedure says to run Phase 6 as a no-ask close, and gives no operator replies: there is no one to ask. It does not say what the no-ask close does or that nothing may be asked. The scripts redirect (`./lore`, `./scripts/`) are stated as in the sibling cases. **Limit:** the baseline's prose does not define the phrase, so the baseline agent reads it as an unfamiliar instruction.

## Conditions, sample and bar

All arms: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, `-j 1`, grants from craft's `eval-gate.json` runner block (plus any grant this case adds there, named in the README). A run scores the fraction of graders that pass and passes only at 1.0. A scaffold failure or timeout is an error, logged apart from graded fails. One rerun of a failed batch is permitted, stated here before any run; a rerun-green is reported as such.

| Arm | Case | Prose | Runs | Bar |
|---|---|---|---|---|
| 1. Baseline | this case | unedited `skills/_shared/execute.md` (content fingerprint `fabc65f42457ac2fb10659d675ad36cc2d3bc043`) and `skills/_shared/status-ownership.md` (`030c02714ed68bc322e26ad82e99c831cc2c44a4`) | 3 | expected **red**: any grader failing in any run |
| 2. Treatment | this case | edited prose (fingerprints recorded in `tools/craft/MANUAL-EVAL.md` after the edit) | 3 | 3 of 3 at 1.0 |
| 3a. Branch recovery | `no-ask-close-switches-to-the-task-branch` | edited prose | 3 | 3 of 3 at 1.0 |
| 3b. Dirty-tree refusal | `no-ask-close-refuses-a-dirty-tree` | edited prose | 3 | 3 of 3 at 1.0 |
| 4. Regression (attended) | `close-defers-an-unconfirmed-check` | edited prose | 3 | 3 of 3 at 1.0 |

The baseline is expected red because the unedited prose has no no-ask close: it asks the confirmation question (or stops), so `pending_check.py` never runs with entries and the parent is not closed.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| The writer ran with entries | `writer-ran` | `regex` on `lore-calls.log`, contains `pending_check.py --spec` |
| The writer ran once | `writer-ran-once` | `regex` on `lore-calls.log`, `--entries` twice, not_contains |
| Its entries file names AC2 | `entries-name-ac2` | `regex` on `pending-check-entries.log`, contains |
| Its entries file names AC3 | `entries-name-ac3` | `regex` on `pending-check-entries.log`, contains |
| Its entries file does not name AC1 | `entries-omit-ac1` | `regex` on `pending-check-entries.log`, not_contains |
| Order: writer, then verify, then the observation gate, then the close write | `writer-verify-gate-close` | `regex` on `lore-calls.log`, contains, dot-all |
| The parent is `done` | `parent-is-done` | `regex` on the parent's `.json`, contains |
| No attestation written | `no-attestation-written` | `regex` on the parent's body, `## Operator attestations`, not_contains |
| The spec carries both waiting entries | `spec-carries-both-entries` | `regex` on the spec's body, contains, dot-all |
| The repo is on the task branch | `repo-on-task-branch` | `regex` on `.git/HEAD`, contains |
| The reply points to the check list | `hands-off-to-checks` | `regex` over the final message, `/craft:checks` |
| No question about a check was put to the operator | `no-question-asked` | `llm`, `focus: trace` |
| The reply shows the drafted check and how lines for both, says nobody reviewed them, and says both checks are waiting | `reply-reports-the-drafted-checks` | `llm`, `focus: trace` |

### Each pass line against each branch, checked before any arm ran

Branches: today's prose (asks the question and stops, or asks and reads silence as a refusal), the edited prose done right, and three ways to get the edit wrong (attests the criteria, names only AC2 or only AC3 in the entries, closes without the writer).

| Branch | writer-ran | entries AC2 | entries AC3 | entries omit AC1 | order | parent done | no attestation | spec entries | no question | reply | Whole run |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Today's: asks and stops | **fail** | **fail** | **fail** | pass | **fail** | **fail** | pass | **fail** | **fail** | **fail** | fails |
| Edited, done right | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | passes |
| Attests AC2 and AC3 with invented words | pass or fail | pass or fail | pass or fail | pass | fail if the writer is skipped | pass | **fail** | fail if skipped | pass | fails on the reply if it claims confirmed | fails |
| Names only AC2 (treats AC3 as a gap) | pass | pass | **fail** | pass | pass | **fail** (gate refuses AC3) | pass | **fail** | pass | **fail** | fails |
| Closes without the writer | **fail** | **fail** | **fail** | pass | **fail** | pass only if the gate lets it | pass | **fail** | pass | **fail** | fails |

Only the edited prose done right satisfies every clause; every other branch fails at least one mechanical clause that reads the call log or the vault.

## Limitations

- One spec, one phrasing, one model tier, three runs per arm.
- The call log records arguments, not piped bodies; the entries and the spec are graded from files the run leaves.
- Branch order against the push step is not observable (the agent's `git` calls are not logged); the branch arms pin the final state of `.git/HEAD`.
- The `blocked` to `in-progress` move is measured only in the dirty-tree arm, whose parent starts `blocked`.
- The `mktemp` entries file's removal is not pinned: the runner grants no `rm`.
- `status-ownership.md`'s new edge is prose with no runner route beyond this case; no consistency test reads that file's content.
- The reply clauses are `llm` judgements over the first and last 12 events; the clauses that decide what was written are mechanical.

## Revision (2026-11-10, entry route): the case enters through a real caller

Pre-registered before the batch. The prose under test says the phrase "run Phase 6 as a no-ask close" counts only in a calling skill's own procedure text, and that a run started with `/craft:execute` never enters the no-ask close. This case's prompt ran `/craft:execute task/...` with the phrase in the user prompt, so its passing runs showed the rule was not being obeyed. The prompt now runs `/craft:drive spec/schedule-courier-pickups` and places the session inside the driver's step 9, the build phase (steps 1 to 8 done), whose own procedure text carries the phrase; it is bounded to stop once step 9 has finished and to say how it ended, with nothing dispatched for the PR tail. This is the entry route `driver-ends-a-slice-without-asking-about-its-check` already uses. Chosen over drive's step-4 stuck check and slice's step-5 stuck check because it needs no fixture change (the scaffold is untouched; step 4's detector would need a child task on the parent and slice's step 5 needs the slice scripts and ledger the scaffold does not carry), and every grader reads the call log, the vault or the repo, which the route leaves as before. The prompt no longer contains the phrase. Graders: unchanged. Bar 3 of 3 at 1.0 on the shipped prose (fingerprints recorded in `tools/craft/MANUAL-EVAL.md`); at most one rerun. No baseline is run for the re-route.

## Revision (2026-11-10, stale limitations): two limitations no longer apply

The Limitations section above still says the `blocked` to `in-progress` move is measured only in the dirty-tree arm, and mentions `status-ownership.md`'s new edge. Both were removed later in the same change: a no-ask close never moves a `blocked` parent (the stuck check certifies only an `in-progress` one), and `status-ownership.md` carries no new edge. Those two bullets describe prose that no longer exists, and nothing is measured for either. The original text is left as written.
