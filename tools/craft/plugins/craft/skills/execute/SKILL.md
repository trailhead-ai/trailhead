---
name: execute
description: >
  Use when executing an approved implementation plan slice-by-slice, dispatching `assumption-prover`
  and `executor` subagents for each slice rather than building inline. The controller (you)
  orchestrates; subagents do the work.
  TRIGGER when: user says "execute", "execute the plan", "start building", "let's build", "build it",
  "implement this", "run the plan", "work the slices", "start the slices", "go" (following plan approval),
  "ship it", or resumes a plan with unfinished slices. Also triggers as the natural
  handoff after `/planning` when the user approves the written plan.
  DO NOT TRIGGER when: no plan exists yet (use `/planning` or `planner` first), the plan has ≤2 slices
  with no unknowns and small scope (just build it yourself), or the user is debugging rather than
  executing.
---

# Execute

Execute a plan slice-by-slice. For each slice, resolve unknowns first, then build.
Every status write below follows the task-status ownership contract in
`../_shared/status-ownership.md` — the single source of truth for who writes each
status value, `done`'s push guarantee, and `blocked` semantics; read it before
touching status.

**Three subagent roles** — all dedicated agents, no inline prompt templates:

| Role | Agent | Purpose |
|------|-------|---------|
| Resolve unknowns | `assumption-prover` | Writes a TDD test that proves or disproves an assumption |
| Build slices | `executor` | Writes tests first, implements, self-reviews, commits |
| Review work | `drift-gate` | Per-slice conformance only — plan delivered, status claim holds, next slice unblocked |

The controller decides which to dispatch and absorbs findings between iterations.

## When to Use

- You have an approved implementation plan with slices and known unknowns
- You want to execute in the current session

## Skip Gate

**Don't use subagents when:**
- The plan has ≤2 slices, no unknowns, and the total scope is small (≤100 lines expected)
- You'd spend more time writing prompts and absorbing reports than just building it

In those cases, build it yourself following TDD and verification. Subagent overhead isn't free.

## The Loop

A plan is a parent `task` record whose slices are child `task` records wired with `parent`
(containment) and `depends-on` (ordering) edges. Walk the graph in **topological order** and
dispatch **leaf tasks only** — a child task is workable when it is `ready` and every task it
`depends-on` is `done`. `lore task graph <parent-name>` renders the containment subtree,
per-task status, and marks the runnable leaves; use it to pick the next task and to see
progress. **Never dispatch the parent task itself** — it is the container and the lifecycle
handle, not a unit of work.

### Determine the task shape

Before walking the loop, determine the task's shape. Run `lore task graph <parent-name>`
first and read both the render and the record's sidecar:

- **Standalone.** The graph renders exactly one line for the task AND the record's
  sidecar carries no `parent` edge. There is no child to descend into — the task is the
  whole unit of work. See the standalone branch below.
- **Parent-with-children.** The graph renders more than one line. Proceed with the Loop
  exactly as written below (steps 1-6) — unchanged.
- **Ambiguous.** A single-line render **with** a `parent` edge present matches neither
  case above and is never classified silently — most often a mis-wired parent edge (a
  `parent` value that wasn't passed as a bare task name silently renders as a detached
  node; see [[lesson/lore-task-graph-parent-depends-on-require-bare-task-names]]).
  Stop and report the suspected mis-wired parent edge, citing that lesson.

**Standalone branch:**

- **`ready`** — dispatch only when the node carries the `(runnable)` marker. A
  standalone node rendered without the marker has an unmet `depends-on` edge — report it
  and stop rather than guessing. When runnable, treat the task itself as the one slice:
  run step 3 (dispatch `executor`) and step 4 (review, scaled to the size table) below
  against it, then skip straight to [After All Slices](#after-all-slices) — there is no
  next leaf to pick and no parent-lifecycle flip to perform.
- **`open`** — run the `_shared/refine.md` procedure inline. Pass `--interactive` only
  when execute itself has a human channel to a live operator right now; otherwise run it
  unattended. Refine promotes cleanly → proceed as the `ready` case above. Refine
  escalates (writes `## Refine — unresolved`) or routes to `/craft:plan` /
  `/craft:brainstorm` → stop and report the refine outcome; do not dispatch an executor
  against a task that did not promote.

### Resuming a run

Invoking execute against a task already `in-progress` **resumes** it — never refuses, never
restarts from scratch. You already have the task in hand and need its branch, so read the
`craft/branch` label straight off the task record, falling back to a locally-present branch
matching the task name; then pick up wherever the graph and workspace show the run left off.
(The `label.craft.branch:` search query runs the other direction — branch to tasks — and
belongs to the operator sweep in `../_shared/status-ownership.md`, not here.)

**A task carrying `craft/push=failed` is never resumed** — workspace intact or not. The
label means its commits are un-pushable, not that the run crashed, so re-running the build
would paper over the real problem: skip it and report it. It keeps the status it was left
with until the push is settled by hand and the guard cleared with
`lore record update task/<name> --unset-label craft/push`.

Otherwise, if the task's workspace no longer exists, reconcile before resuming: the task is
resumed when its branch is recoverable (check out the branch named by `craft/branch` and
continue from the claim in [Claiming the run](#claiming-the-run-at-first-dispatch) onward)
or released back to `ready` when it isn't — `lore record update task/<name> --status ready`.
Whichever path is taken, append a one-line breadcrumb to the task body —
`reconciled: resumed from <branch>` / `reconciled: released to ready` — with
`lore record update task/<name> --diff`, piping a unified diff whose only hunk adds that
line. Use the `--diff` form, not a bare `lore record update`: bare stdin is a **full-body
replace**, so writing the breadcrumb that way destroys everything else in the record.

Two writes move the task off `in-progress`: [Phase 6](#phase-6-close-and-completion-report)'s
`done` and each escalation site's `blocked` write. An escalation *answered* in-session writes
no status at all — the run simply continues, and the task legitimately holds `in-progress`
until one of those two lands. Full writer and exit-owner rules live in
`../_shared/status-ownership.md`.

### Claiming the run at first dispatch

**Before this run's first dispatch of any agent** — the `assumption-prover` in step 1 counts
just as much as the `executor` in step 3 — claim the plan in one command: flip the parent
off `ready` and write its branch label together —
`lore record update task/<parent-name> --status in-progress --label craft/branch=<bare-branch>`
(bump `updated:` to today). Write both at dispatch, not only at close — `craft/branch`'s
primary reader is crash-resume logic, which runs on tasks that never reached close. Skip if
the parent is already `in-progress`, `done`, `dropped`, or `superseded` — if it's
`in-progress` from an earlier session, see [Resuming a run](#resuming-a-run) above instead
of re-dispatching from scratch.
For each runnable child task (a slice):

### 1. Does this slice have an unresolved unknown?

**Yes → dispatch `assumption-prover`.**

The agent expects: plan path, the unknown (specific and restated), why it matters (which slice is blocked), working directory.

It returns: VALIDATED / INVALIDATED / NEEDS_CONTEXT / BLOCKED, plus evidence, test files to clean up, and surprises.

**No → skip to step 3**

### 2. Absorb findings

- **VALIDATED:** update the plan, check off the unknown. Carry the **test files to clean up** from the prover's report into the executor dispatch so it removes them after building proper tests.
- **INVALIDATED:** pause, report to user, reassess. The design may need to change. Do NOT proceed to build — see [Handling Assumption-Prover Status](#handling-assumption-prover-status). **If the run ends here:** write `blocked` on the plan — `lore record update task/<parent-name> --status blocked --diff`, piping a unified diff that **appends** the blocked note (bare stdin is a full-body replace and would destroy the record) — plus the [Phase 5](#phase-5-flow-out) scrub and, if commits exist, the [Phase 6](#phase-6-close-and-completion-report) blocked-path push and its standalone `craft/branch` write; body-content contract and full rules in `../_shared/status-ownership.md`.
- **Surprises:** if the prover discovered new unknowns, add them to the plan. Decide whether they block the current slice or a future one.

### 3. Dispatch `executor`

The agent expects:
- Plan path and task name
- Proven unknowns summary (or "None")
- Assumption-prover tests to clean up (or "None")
- Working directory

Executor figures out implementation steps — don't over-specify the *how*. Specify the *what*.

Default model is Sonnet. Override per-dispatch when needed:
- `model: "opus"` for integration-heavy slices (3-5 files, cross-module coordination)
- Re-dispatch with Opus if a Sonnet attempt returns BLOCKED with unclear cause and `troubleshooter` confirms the issue is reasoning capacity

Returns: DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED. See [Handling Executor Status](#handling-executor-status).

### 4. Review (scaled to change size)

| Change Size | Review Approach |
|-------------|----------------|
| **Small** (≤30 lines, 1-2 files) | Skip formal review. Review inline or one quick check. |
| **Medium** (30-200 lines, 3-5 files) | Dispatch `drift-gate` for a conformance pass. |
| **Large** (200+ lines or 5+ files) | Dispatch `drift-gate` for a conformance pass. Dispatch a second pass only when the first returns saturated/over-length. |

When dispatching `drift-gate`, give it: plan path + task name, the executor's status report (so it can verify the claim), and base/head SHAs for the diff.

Absorb the reviewer's verdict — `PASS` | `DRIFT` | `BLOCKED` — plus its findings into your working context. If it emits a `Security-surface:` line, carry it forward into a running list for the whole-change security trigger (the Security phase in [After All Slices](#after-all-slices)) — do not act on it per-slice.

Quality, style, and design review are explicitly out of scope for `drift-gate` — that's deferred to the whole-change phases in [After All Slices](#after-all-slices), not dropped entirely.

### 5. Update the task graph

After each child task completes (or each unknown resolves), record the state on the graph — the task graph is the source of truth for what's done and what's left, so the run stays resumable if context breaks (handoff, new session):

- **Task status.** Set the child task you just built to `done`: `lore record update task/<name> --status done`. Advancing a task off `ready` is what makes its dependents runnable. A child's `done` here is bookkeeping only — committed on the task branch; the push guarantee that makes `done` mean "committed and pushed" attaches to the run's close (see [Phase 6](#phase-6-close-and-completion-report) and `../_shared/status-ownership.md`), not this step.
- **Parent lifecycle.** The parent already flipped `ready → in-progress` at the run's first dispatch (see [Claiming the run](#claiming-the-run-at-first-dispatch)) — that's what keeps the task graph honest from the moment code starts shipping against it, rather than waiting for a child to land first. Nothing to write here; if the parent still reads `ready` this far into the run, that's a sign the claim was skipped and should be run now.
- **Unknowns.** Check off resolved unknowns in the parent's Known Unknowns block; add any new unknown discovered during the slice, noting which child task it blocks.
- **Design changes.** Note any design change a finding forced — in the parent body, and by re-shaping child tasks per the split/append ritual below. Body text written here is record text: run it through the [Phase 5](#phase-5-flow-out) credential scrub first, exactly as a session candidate would be.

**Per-cycle working set:** the controller's working set is the current child task plus the parent's Known Unknowns block. The controller does not re-read the whole graph each cycle — it updates incrementally and re-reads only `lore task graph <parent-name>` to pick the next runnable leaf.

### 6. Next task

Re-run `lore task graph <parent-name>`, pick the next runnable leaf (topological order), and repeat from step 1. When no runnable leaf remains and every child is terminal, go to [After All Slices](#after-all-slices).

### Splitting and appending tasks

The graph is not frozen at plan time — reshape it as reality demands:

- **Split** — a slice turns out too large or has an internal ordering. Create the new child tasks (`lore record create --kind task … --parent <parent-name>`), wire `--depends-on` edges between them and to the original's dependents, then set the original task `superseded` (or `dropped`) so it drops out of the runnable set. Note why in the parent body.
- **Append** — new work surfaces mid-execution. Create a fresh child task under the parent with the right `--depends-on` edges; the ordering sequences it automatically.

Both keep the graph — not a prose list — the single source of truth for what's left.

## After All Slices

Every child task is terminal (`done`/`dropped`/`superseded`). The whole change now runs a **sequential phase pipeline** — simplify → correctness → conditional-security → flow-out → close. Run the phases **in order**; each builds on the settled state of the one before it. Record progress as you go (see [Phase progress and resumability](#phase-progress-and-resumability)) so a broken context can resume mid-pipeline.

Fix `base` once at the start: it is the commit the whole change started from (the parent's pre-execution SHA). `HEAD` is the current tip. The phases operate on the whole-change `base..HEAD` diff, not any single slice.

### Standalone task adaptations

When the task shape (above) was standalone, this phase pipeline still runs — adapted to
operate on the task record itself rather than a parent:

- The task carries its own `## Flow-out` checklist — refine wrote it when the task
  promoted to `ready` — so Phase 5 works that checklist directly.
- **`## End Phases` is created at the first executor dispatch** on a standalone task, not
  deferred until pipeline entry — this widens its normal end-pipeline-only lifecycle so
  the dispatch-count note (below) has somewhere to live from the start, even before the
  phase pipeline itself begins.
- Phase 6's completion guard passes trivially: a standalone task has no children, so
  there is nothing non-terminal to block the close.
- **Simplify (Phase 2) skips by default.** Run it anyway when any of:
  - (a) the change hits the existing Large bar (200+ lines or 5+ files);
  - (b) the executor was dispatched more than once on the task —
    record the running dispatch count in the task's `## End Phases` notes as it
    happens, so the trigger survives a resumed run;
  - (c) the executor returned `DONE_WITH_CONCERNS` naming duplication/scaffolding/structure.

  The completion report names the fired trigger, or says exactly
  "skipped — single leaf, no trigger" when none fired.

Phases 1, 3, 4, 5, 6 are otherwise unchanged; the parent-with-children path is untouched
beyond the task-shape branch above.

### Phase 1: Test-runner gate

Dispatch `test-runner` for each applicable suite (the project's test run and lint/typecheck/CI checks) rather than running inline. Keeps the noisy test output out of your main context and returns a concise pass/fail. The pipeline does not advance to simplify until this gate is green — every later phase assumes a green baseline.

### Phase 2: Simplify

Record the current `HEAD` as the **pre-simplify SHA**, then dispatch `simplifier` with: base SHA, pre-simplify SHA, plan path (and spec path if the plan references one), working directory. It removes cross-slice duplication and dead scaffolding the incremental build left behind, re-greens the full suite, and commits its change separately (GPG-signed).

When it returns, **re-run the guard yourself against a clean working tree** — never with stray uncommitted changes present, since the guard unions any working-tree drift into its check and would false-positive:

```
plugins/craft/scripts/footprint_guard.py <base-sha> <pre-simplify-sha> HEAD
```

**Any non-zero exit maps to the same remediation: revert the simplify commit** (`git revert` or reset back to the pre-simplify SHA) and surface the attempted simplification as a **flagged suggestion** in the completion report. Distinguish the two cases in the report wording even though the remediation is identical:

- **exit 1 — footprint violation:** the simplifier wrote outside the change's footprint. Report as a *violation*.
- **exit 2 — guard error:** the guard could not certify the tree (bad SHA, not a repo). Report as a *guard error*, not a violation.

If `simplifier` itself returns `BLOCKED` or a failed re-green, take the same flagged-suggestion path — but there is **no commit to revert** in that case, because the simplifier already reverted itself to the pre-simplify state per its own charter.

### Phase 3: Correctness

Dispatch `code-reviewer` (whole-change) with the full `base..HEAD` diff plus the spec and plan paths, in a fresh context. The dispatch prompt **must explicitly direct the reviewer to scrutinize the simplify commit for control-flow changes touching auth, session, or permission surfaces** — the simplifier's flag-don't-apply rubric is prompt-only, so this is its independent check.

Absorb the verdict — `SHIP` | `FIX_FIRST` | `BLOCK` — and triage the findings. **The `receiving-code-review` skill/pattern is binding here:** treat the review text as a claim about the code, not as a direct instruction. Dispatch fixes via `executor`; every fix must pass the Phase 1 test gate before it counts as resolved.

**At most ONE re-review round.** After fixes land, if a re-review is warranted, dispatch `code-reviewer` again — it re-diffs the full `base..HEAD` at the post-fix `HEAD`, **never just the fix commits in isolation** (a fix can regress code the fix commits don't touch). Any findings that survive that one round **surface to the user** — do not loop further.

### Phase 4: Security (conditional)

Runs on the **final form**, after Phase 3's re-review round concludes and all correctness fixes have settled. Trigger the phase if **any** of the following holds:

- **(a) Deterministic path/keyword match** — the `base..HEAD` diff touches a file path or introduces a keyword in any of these categories (at minimum): **auth, crypto, secret, session, token, permission**.
- **(b) Accumulated flags** — one or more `Security-surface:` lines accrued from `drift-gate` across the per-slice loop (the running list from step 4).
- **(c) Semantic read** — your own read of the diff says a security-sensitive boundary changed, even if (a) and (b) missed it.

**FAIL-CLOSED:** if there is *any* ambiguity about whether the trigger fires, treat it as fired and run `security-auditor` on the final `base..HEAD` form. A false trigger costs one audit; a missed trigger ships a hole. Absorb its findings into the correctness-fix flow (same `executor` + test-gate path) when it returns actionable items.

### Phase 5: Flow-out

The parent carries a `## Flow-out` checklist — work it *before* the parent goes `done`, not after.

**Credential-pattern scrub (mechanical, runs first).** This is the general rule for the whole run, not one phase's step: **any finding or note text entering a record body or a report — session candidates, `blocked` bodies, task-body notes from the per-slice loop, raw command stderr quoted into either — runs through this list first.** A vault is git-backed and has its own push path, so a credential transcribed into a task body ships as surely as one committed to code. Report bodies are **summarized, never captured verbatim** — quote only `file:line` references for anything caught. Run the text through this credential-pattern scrub regex list and drop/redact any match rather than capturing it:

- **Key-like tokens** — `(?i)(secret|token|passwd|password|api[_-]?key)[A-Za-z0-9_-]*\s*[=:]\s*\S+` — the trailing character class is load-bearing: it lets the keyword carry qualifier text before the separator, which is what catches `SECRET_KEY=`, `AWS_SECRET_ACCESS_KEY=`, and `API_KEY_ID=`. A keyword anchored straight to `[=:]` walks past every compound name.
- **Vendor fixed-prefix tokens** — `(?i)\b(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}|glpat-[A-Za-z0-9_-]{20}|xox[baprs]-[A-Za-z0-9-]+|sk_live_[A-Za-z0-9]+|AIza[0-9A-Za-z_-]{35})\b` — issuer-shaped credentials identifiable on their own, with no `key=` preamble to trip the pattern above
- **Bearer / api-key shapes** — `(?i)bearer\s+[A-Za-z0-9._\-]+`, `(?i)api[_-]?key['"]?\s*[:=]\s*['"]?[A-Za-z0-9._\-]{16,}`
- **High-entropy literals** — `\b[A-Za-z0-9+/]{32,}={0,2}\b` (base64/hex-shaped secrets), `\b[A-Fa-f0-9]{40,}\b`
- **PEM private-key blocks** — `-----BEGIN [A-Z ]*PRIVATE KEY-----` (the high-entropy pattern catches the body but not this header, so pin it separately)

Prefer over-matching to under-matching: this list is a tripwire, and a false hit costs one manual look. **Known blind spot:** a binary file's diff renders as `Binary files … differ` rather than content, so a credential inside a binary artifact is invisible to every pattern above — a change that adds binary files needs a manual look regardless of what the patterns return.

Then complete the ritual:

- **Update touched area/subsystem profiles** with what actually changed (via the `lore` CLI), so the next agent inherits current ground truth.
- **Capture prover-validated assumptions** and any decisions / lessons / follow-ups surfaced during the build as **session candidates** (`lore session candidate …`) — they become durable records at flush.
- **Tick the parent's `## Flow-out` checklist** to reflect what you did.

### Phase 6: Close and completion report

**Push before close.** Before the close phase of a run writes `done`, every member repo carrying commits on the task branch must be pushed. Enumerate the repos from the workspace, don't guess at them: in a camp workspace they are the member worktrees of the current workspace as listed in its camp manifest (`manifest.json`, the same set `camp status` reports); in vanilla usage the set is the single current repo. Detection is then a named inline mandate, not an inference: per repo, run `git log origin/<branch>..HEAD --oneline`. Empty output means nothing to push in that repo. Non-empty output means push is required — and so does an **error**, since `git log` fails outright rather than printing nothing when `origin/<branch>` doesn't exist as a remote-tracking ref; a branch with no upstream counts as unpushed either way.

**Auto-push covers task branches only.** If the run is sitting on the repo's default branch — a run that started on `main`/`master` with explicit user consent — do not auto-push it. Name the branch and its unpushed commits in the completion report and leave the push to the user.

Before pushing a repo with unpushed commits, run the pre-push secret scan: check `git log origin/<branch>..HEAD -p` for that repo against the credential-pattern scrub list ([Phase 5](#phase-5-flow-out)).

**The scan is fail-closed: a command that errors is never a clean scan.** Empty output counts as "clean" only when the command also exited successfully. The trap is the first push of every task branch — until a `--set-upstream` push lands there is no `origin/<branch>` remote-tracking ref, so the scan command fails outright and prints *no diff at all*, which is indistinguishable from a clean result by output alone. When `origin/<branch>` does not exist, the entire branch is about to be published: scan everything not already on the remote instead, with `git log HEAD --not --remotes=origin -p`. That form needs no upstream, lists the full outgoing history, and goes empty on its own once the branch is pushed.

On a match, do **not** push that repo. Take the blocked path instead — `lore record update task/<parent-name> --status blocked --diff`, piping a unified diff that **appends** the blocked note — and name the remediation in the report: rotate the credential, then rewrite history before attempting the push again.

**This scan gates every push, the blocked path's included.** Each escalation site's "(if commits exist)" push routes through this phase, so it inherits the scan: a run blocked *because* the scan hit stays unpushed until the credential is rotated and the history rewritten. Going `blocked` never licenses shipping the flagged commits.

**The blocked path pushes; it never closes.** Its `craft/branch` write is a standalone command against whichever record just took `blocked` — `lore record update task/<name> --label craft/branch=<bare-branch>`, or `task/<parent-name>` when the plan is what blocked — **not** the close command below, which also writes `--status done`. A slice-level block is never licence to close the parent.

For every repo that clears the scan, push with `git push --set-upstream origin HEAD` (verbatim — a bare `git push` never converges without an upstream). Push is idempotent: an already-up-to-date branch counts as success, so retrying after a failed status write is safe — the status write is the completion signal, the push is its precondition.

On push failure (auth, no remote, rejection) at close: the task stays `in-progress` — the honest state — and the session labels **the run's task record**, `task/<parent-name>` on this pathway: `lore record update task/<parent-name> --label craft/push=failed`. That record is the one holding `in-progress` and the one resume logic reads; a child slice's record is bookkeeping, not the run's lifecycle handle, so labelling it would leave the guard where nothing looks for it.

**A blocked-path push failure gets the same label and no status reversion.** The record keeps the `blocked` just written for it — it does *not* revert to `in-progress` — and gains `craft/push=failed` alongside: `lore record update task/<name> --label craft/push=failed` (`task/<parent-name>` when the plan is what blocked). The label, not the status, is what tells resume logic the commits are un-pushable.

Either way the completion report names the failure **and the remediation**, distinguishing a non-fast-forward rejection (needs reconciliation with the remote, not credentials) from an auth/no-remote failure (needs credentials or a configured remote, not reconciliation). The remediation is not finished when the push finally lands: nothing clears the guard automatically, so settling the push by hand also means `lore record update task/<name> --unset-label craft/push` — a stale guard makes every later resume skip-and-report. Run any raw git stderr through the credential-pattern scrub before it enters report or record text.

**Close the run.** With every child terminal, the flow-out checklist ticked, and every repo's push settled, set the parent `done`: `lore record update task/<parent-name> --status done`, re-asserting `--label craft/branch=<bare-branch>` at close. The completion guard refuses this while any child is non-terminal (it names them); a parent closed without a `## Flow-out` section gets a non-blocking flow-out reminder — treat that reminder as a sign the ritual above was skipped, not as a nuisance.

**Completion report.** Report to the user and stop. Do **not** automatically invoke `/portage:pull_request` — the user decides when to open a PR. The report must **enumerate every phase's outcome explicitly, even when a phase was clean, empty, or skipped** — a phase with nothing to say still gets a line, so a reader can tell it ran. Worked example:

> simplify: no changes; correctness: SHIP, 0 findings; security: skipped — no trigger; push: 2 repos pushed, 1 already up to date

**Measurement tally.** For each correctness Critical/Important finding, record it **cited against the specific plan section it was classified under** — not a bare count. Each finding is classified **local-to-one-slice** (a defect that lives inside a single slice's delivers) vs **cross-slice** (a defect only visible across slice boundaries), and the citation must be spot-checkable: name the plan section, not just the digit. **Revisit condition:** if more than 2 local-to-one-slice Criticals accrue across the first 5 executed plans post-rollout, restore the per-slice quality charter. This is a stated, not-yet-mechanically-enforced condition — record the tally each plan; do not auto-restore.

### Phase progress and resumability

Record phase progress as an `## End Phases` checklist appended to the **parent task body** (via `lore record update`), one line per phase, ticked as each completes. This is what makes the pipeline resumable if context breaks mid-run: on resume, read the checklist and re-enter at the first unticked phase.

**Re-entering any end phase on resume requires a clean-working-tree precondition.** A dirty tree found on resume — staged, unstaged, or untracked changes from a mid-mutation crash — is **reverted to the last recorded phase boundary** (the SHA the last ticked phase left `HEAD` at) before any re-dispatch. This is what prevents footprint corruption: `footprint_guard.py` unions live working-tree drift into its check, so a stray uncommitted edit from a crashed mutation would otherwise false-positive the guard or, worse, get folded into a later commit. Never re-dispatch a phase onto a dirty tree.

## Model Selection

Defaults are baked into each agent's frontmatter. Escalate when signals say you should.

| Role | Default | Escalate to |
|------|---------|-------------|
| `assumption-prover` | Sonnet | Sonnet/high if the unknown spans multiple subsystems or needs deeper code exploration |
| `executor` | Sonnet | `model: "opus"` per-dispatch for integration-heavy slices |
| `drift-gate` | Sonnet/high | (already pinned, no override needed) |

**Escalation signals:**

- Executor returns `BLOCKED` with unclear cause → dispatch `troubleshooter` (Opus/high) to diagnose before re-dispatching the executor. **If the run ends here:** write `blocked` on the slice — `lore record update task/<name> --status blocked --diff`, piping a unified diff that **appends** the blocked note (bare stdin is a full-body replace and would destroy the record) — plus the [Phase 5](#phase-5-flow-out) scrub and, if commits exist, the [Phase 6](#phase-6-close-and-completion-report) blocked-path push and its standalone `craft/branch` write; body-content contract and full rules in `../_shared/status-ownership.md`.
- Executor returns `DONE_WITH_CONCERNS` repeatedly on the same slice → re-dispatch with `model: "opus"` or break the slice smaller.
- Assumption-prover returns `NEEDS_CONTEXT` → it's not the model, it's the prompt. Give it more context and re-dispatch at the same tier.

**Why not Opus everywhere:** Opus is the most capable but also the slowest and most expensive. Sonnet is more than enough for mechanical TDD work. Reserve Opus for reasoning-heavy roles (review, troubleshooting, architecture) where fresh eyes matter.

## Handling Assumption-Prover Status

**VALIDATED:** Proceed to build the slice.

**INVALIDATED:** Do NOT build. Report to user with the evidence. Options:
1. **Minor adjustment** — the design holds, just one child task changes. Update the affected child task record (`lore record update task/<name> …`), note what changed and why — running that note through the [Phase 5](#phase-5-flow-out) credential scrub, since it lands in a record body — and continue.
2. **Design change** — the invalidation affects multiple child tasks or the architecture. Re-enter planning: dispatch the `planner` subagent (isolated, Opus) or invoke the `planning` skill inline. Do NOT use `EnterPlanMode` — plan mode blocks writes to the plan vault.
3. **Drop the task** — the feature doesn't need this part. Reshape the child task record to `superseded` (or `dropped`) via `lore record update`, note why, continue with remaining tasks.

**If the run ends here** (none of the above resolves it in-session): write `blocked` on the plan — `lore record update task/<parent-name> --status blocked --diff`, piping a unified diff that **appends** the blocked note (bare stdin is a full-body replace and would destroy the record) — plus the [Phase 5](#phase-5-flow-out) scrub and, if commits exist, the [Phase 6](#phase-6-close-and-completion-report) blocked-path push and its standalone `craft/branch` write; body-content contract and full rules in `../_shared/status-ownership.md`.

If the INVALIDATED result is surprising (behavior you thought was standard turns out to differ), that may also be a `troubleshooter` question: dispatch it to figure out *why* the assumption was wrong before reshaping the plan.

**NEEDS_CONTEXT:** Provide missing context and re-dispatch.

**BLOCKED:** Assess — provide more context, use a more capable model, or escalate to user. **If the run ends here:** write `blocked` on the slice — `lore record update task/<name> --status blocked --diff`, piping a unified diff that **appends** the blocked note (bare stdin is a full-body replace and would destroy the record) — plus the [Phase 5](#phase-5-flow-out) scrub and, if commits exist, the [Phase 6](#phase-6-close-and-completion-report) blocked-path push and its standalone `craft/branch` write; body-content contract and full rules in `../_shared/status-ownership.md`.

## Handling Executor Status

**DONE:** Proceed to review.

**DONE_WITH_CONCERNS:** Read concerns. If about correctness/scope, address before review. If observations, note and proceed.

**NEEDS_CONTEXT:** Provide missing context and re-dispatch.

**BLOCKED:** Assess the blocker:
1. Context problem → provide more context, re-dispatch
2. Needs more reasoning → re-dispatch with `model: "opus"`
3. Slice too large → break into smaller pieces
4. Plan is wrong → escalate to user. **If the run ends here:** write `blocked` on the plan — `lore record update task/<parent-name> --status blocked --diff`, piping a unified diff that **appends** the blocked note (bare stdin is a full-body replace and would destroy the record) — plus the [Phase 5](#phase-5-flow-out) scrub and, if commits exist, the [Phase 6](#phase-6-close-and-completion-report) blocked-path push and its standalone `craft/branch` write; body-content contract and full rules in `../_shared/status-ownership.md`.
5. Cause unclear → dispatch `troubleshooter` to diagnose before re-dispatching the executor. Don't keep re-dispatching the same prompt hoping for a different outcome.

**If the run ends here** (1–3 or 5 above didn't resolve it): write `blocked` on the slice — `lore record update task/<name> --status blocked --diff`, piping a unified diff that **appends** the blocked note (bare stdin is a full-body replace and would destroy the record) — plus the [Phase 5](#phase-5-flow-out) scrub and, if commits exist, the [Phase 6](#phase-6-close-and-completion-report) blocked-path push and its standalone `craft/branch` write; body-content contract and full rules in `../_shared/status-ownership.md`.

## Red Flags

**Never:**
- Build a slice before its unknown is resolved
- Proceed after an invalidated assumption without user input
- Skip review for medium+ changes
- Dispatch multiple *executor* subagents in parallel on the same slice (they'll conflict on the same files). Parallel dispatch is fine when the agents operate on independent scopes — e.g. one checker per repo.
- Dispatch the parent task as if it were a slice — it is the container and lifecycle handle, never a unit of work.
- Close the parent `done` with non-terminal children, an un-ticked `## Flow-out` checklist, or a task-branch repo still carrying unpushed commits — the completion guard blocks the first; skipping the second traps knowledge; skipping the third breaks what `done` means, which is *committed and pushed*.
- Ignore subagent questions or surprises
- Start on main/master without explicit user consent
