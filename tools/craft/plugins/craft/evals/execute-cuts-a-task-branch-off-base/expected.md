# Expected verdict — a fresh execute run cuts its own task branch off the latest base

Written **before** any arm was run, so it cannot be retrofitted to an observed result.

## What is under test

`## The Loop` in `tools/craft/plugins/craft/skills/_shared/execute.md`: on a **fresh** run (the
task carries no `craft/branch` label yet), before the workspace preflight and the claim, execute
fetches the base and cuts a new branch named for the task off it, in the repository the run works
in. On the parent-with-children shape, a design-doc commit plan made on the previous branch is
carried onto the new one. A dirty working tree, or a branch name that already exists locally or on
the remote, stops the run before anything is cut.

Only the vanilla-usage path is exercised: one repository, no camp workspace, base defaulting to
`origin/main`.

## The fixture set

`fixtures/make-fixture-repo.sh <variant> <dest>` materialises a working repo on branch `work` and a
local `origin`. In every variant `work` carries one commit from an earlier unrelated task
(`PRIOR.md`, not on `origin/main`), and `origin/main` has advanced one commit (`UPSTREAM.md`) that
the working repo has **not** fetched. A branch cut from the latest base contains `UPSTREAM.md` and
lacks `PRIOR.md`; a run that builds on `work` has the opposite shape.

| Variant | Task record | Extra state | Correct outcome |
|---|---|---|---|
| `fresh` | `task-ready.md` (standalone) | none | on new branch `fixture-add-a-readme-line`, containing `UPSTREAM.md`, lacking `PRIOR.md` |
| `dirty` | `task-ready.md` | uncommitted edit to tracked `README.md` | **stop**; still on `work`, no local `fixture-add-a-readme-line`, edit intact |
| `collision` | `task-ready.md` | `origin` already has branch `fixture-add-a-readme-line` | **stop**; still on `work`, no local `fixture-add-a-readme-line` |
| `design-doc` | `task-parent-design-doc.md` (parent, `craft/design-doc` label) | plan's `--only` design-doc commit on top of `work` | on new branch `fixture-parent-with-design-doc`, containing `UPSTREAM.md`, lacking `PRIOR.md`, with exactly one commit over `origin/main` touching only `docs/design/fixture-slice.md` |

## Pass condition — the one a harness should read

Graded from the repository on disk by `fixtures/grade.sh <variant> <repo>`, never from the run's
own report. A variant passes when `grade.sh` prints `PASS`. The case passes for an arm only when
all four variants pass. `fresh` and `design-doc` are the discriminating variants; `dirty` and
`collision` exist to catch a treatment that cuts unconditionally.

`grade.sh` was checked before any arm ran: it prints FAIL for `fresh` and `design-doc` on the
untouched fixture, PASS for `dirty` and `collision` on the untouched fixture, and PASS for `fresh`
and `design-doc` after a hand-performed correct cut (fetch, `git switch -c <slug> origin/main`,
cherry-pick of the design-doc commit).

## Expected failure — baseline arm

Against the committed prose at `40c8a63c`, nothing cuts a branch. The baseline arm is expected to
stay on `work` in every variant (it may stop at the workspace preflight for `behind=1` once it
fetches, or proceed to the claim on `work` — both leave `work` checked out). RED: `fresh` and
`design-doc` FAIL; `dirty` and `collision` PASS vacuously.

## How an arm is dispatched

Per `docs/eval-protocol.md`. For each run, a run directory holds the fixture repo at `./repo`
(built by `make-fixture-repo.sh` outside the sandbox) and a copy of the variant's task record. The
arm runs as

```
scripts/eval-sandbox <run-dir> -- claude -p "<prompt>" --setting-sources project \
  --allowedTools "Bash,Read,Glob,Grep" --append-system-prompt "<prose>" < /dev/null
```

where `<prose>` is `execute.md` — `git show 40c8a63c:tools/craft/plugins/craft/skills/_shared/execute.md`
for baseline, the worktree copy for treatment — and `<prompt>` is identical across arms:

> You are the controller running the execute procedure given in your system prompt, against the
> task record at `./<task-file>`. Its `**Labels:**` line stands in for the record's sidecar labels;
> the child task records are not provided. There is no lore vault in this environment: wherever the
> procedure says to run a `lore` command, print the exact command you would run instead of running
> it, and treat any `lore search` as returning no results. This is vanilla usage (no camp
> workspace); the repository is `./repo`, and it is the working directory the run started in. No
> base is configured for this run. Carry out every step the procedure prescribes up to, but not
> including, the first subagent dispatch, running the git commands yourself. Then stop and report
> every command you ran, every lore command you would have run, and whether you are about to
> dispatch or have stopped, and why. Do not edit any file in the repository yourself.

The prompt says nothing about branches. Do not dispatch `craft:execute` or any `craft:*` agent —
those resolve to the live install, not this worktree.

## Runs

One run per variant per arm (4 x 2 = 8). A run that errors (dispatch failure, transient API error)
is discarded and re-run, not scored. The runner writes a `DONE` marker as its last act; a run
without one is not graded.

## Limitations, stated up front

- One run per cell: an anecdote about that run, not a measure of variance.
- Vanilla usage only. The camp-workspace path (target member chosen among several worktrees) is not
  exercised.
- The parent fixture carries its label as a body line, not sidecar data, so the structural label
  read is not measured — only what the run does with the value.

---

# Revision 1 — pre-registered after the baseline arm, before any treatment run

The prose under test signs the carried design-doc commit (`git cherry-pick -S`), matching plan's
own `-S` commit. The fixture had no signing key, so a correct run would have failed at the
cherry-pick for a reason unrelated to the property under test. `make-fixture-repo.sh` now writes a
stand-in signer beside the repo (`<dest>-signer`) and points `gpg.program` at it. The baseline arm
never signs anything, so its four results stand unchanged; the pass condition and the grader are
unchanged.

---

# Results, appended after both arms ran

Baseline: `fresh` FAIL, `design-doc` FAIL, `dirty` and `collision` PASS vacuously. That is the
predicted RED. Treatment: all four PASS. That is GREEN. The per-run detail is in
`tools/craft/MANUAL-EVAL.md`.
