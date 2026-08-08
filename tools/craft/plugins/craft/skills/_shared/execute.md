# Execute — plan and standalone-task build procedure, shared

**Execute** builds an approved implementation plan slice-by-slice, or a standalone
task record as its own single slice, dispatching `assumption-prover` and `executor`
subagents to resolve unknowns and build rather than doing the work inline. This file
is the single source of truth for the procedure — `skills/execute/SKILL.md` is a thin
attended wrapper over it (refine.md's shape), and an unattended caller (a future
ranger drain loop) dispatches this same document with no human channel. Neither
re-inlines the steps; both read them from here, so the two callers can never drift
apart.

# Execute

Execute a plan slice-by-slice — or a standalone task as its own single slice. For each slice,
resolve unknowns first, then build.
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

## Two modes, one procedure

Mode follows the caller. Everything below is identical in both modes **except the
escalation points** named in the table below — every place this procedure would
otherwise ask a human, an unattended caller re-routes through one of two moves:
**escalate-via-park** (write the structured park section onto the run's task record,
take the run `blocked`, and stop) or **proceed-per-contract** (continue on a decision
this document pre-authorizes, rather than asking).

| Invocation | Mode |
|---|---|
| A human running `/craft:execute` in a live session | **attended** — today's behavior; ask the user at each named escalation point |
| A loop session dispatching this procedure with no human channel (e.g. a future ranger drain) | **unattended** — every escalation point re-routes per the table below |

### Escalation points and their unattended re-route

| Attended escalation point | Unattended re-route |
|---|---|
| Assumption-prover INVALIDATED with no in-session resolution ([Handling Assumption-Prover Status](#handling-assumption-prover-status)) | escalate-via-park |
| Executor BLOCKED with the plan judged wrong ([Handling Executor Status](#handling-executor-status)) | escalate-via-park |
| Any other escalation this document names that does not resolve in-session | escalate-via-park |
| The PR/merge decision once a push succeeds | proceed-per-contract — pre-authorized **only** into the portage tail (`updater`/`monitor`); the unattended run never merges and never decides the PR outside that pipeline (see `../_shared/status-ownership.md`) |
| Task-status writes while this procedure runs under a loop | proceed-per-contract — the loop session is the sole task-status writer; a dispatched `executor` never writes status (see `../_shared/status-ownership.md`) |

**Escalate-via-park, in full.** Write this structured section verbatim onto the run's
task record — the answered-predicate's literal heading, so a later answered-blocked
sweep can find it by grep:

```markdown
## Refine — unresolved

**Question:** <the one surviving decision, stated so it can be answered in a sentence>

**Evidence gathered:** <what the run has established so far, as pointers>

**Recommended answer:** <best call, and why>
```

Write it with `lore record update task/<name> --vault <elected-vault> --diff`, piping a unified diff that **appends** the section — bare stdin is a full-body replace and
would destroy the record. Then take the same `blocked` path this document already
prescribes for the escalation that triggered the park (push what exists, re-assert
`craft/branch`, flip `blocked`), and return the run's one-token outcome (below) rather
than waiting on a reply that will never come.

**One-token outcome return.** An unattended dispatch of this procedure returns its
result through an **outcome file**, never through its reply — a subagent's reply is
not a channel any contract can enforce. Write exactly one token naming the run's
terminal state — `DONE` / `BLOCKED` / `NEEDS_CONTEXT` — to the outcome file path the
caller passed at dispatch, and nothing else; commentary written around the token is
unparseable by whatever reads the file.

That vocabulary is this document's **default**, not a floor: a dispatching caller whose
own dispatch instructions pin a different outcome grammar overrides it, and the caller's
grammar wins (ranger's execute drain does exactly this — its per-task agent returns
`PUSHED` / `BLOCKED` / `FAILED` / `SKIPPED` with mandatory arguments). Follow whichever
grammar your dispatch named; if it named none, the three tokens above are it.

**`--vault` is mandatory on every `lore record update` and every `lore record show` in
this procedure.** Both locate a record by a cwd-blind first-match scan across configured
vaults in declaration order; a dispatched agent's cwd is not the operator's, so an
unqualified write can silently land in the wrong vault — and an unqualified *read* can
just as silently answer from a different vault's same-named record, which is how a run
decides a task's shape from someone else's graph. Every literal
`lore record update` and `lore record show` command below names `--vault <elected-vault>`
— the vault the
caller elected, passed at dispatch — so attended and unattended runs never diverge on
which vault gets written. **Attended runs are handed nothing, so bind it once at the
start:** `<elected-vault>` is the vault the task record you are building came from — ask
`lore vault resolve --kind task --json` and read its `vault` field — and if that
disagrees with where you actually found the record, name the record's own vault and say
so rather than guessing.

**Where `--vault` is not offered, the elected vault still governs.** `lore task graph`,
`lore record create`, and `lore session candidate` take no `--vault` flag — do not invent
one; a rejected flag stops the run. For those, name the routing scope the elected vault
corresponds to (`lore record create --team <scope>` and its `--repo` / `--product` /
`--suite` siblings) where the command offers it, and where it offers nothing, check the
result against `<elected-vault>` before acting on it: a `lore task graph` render that does
not match the vault the task came from is the ambiguous case below, not a fact.


## When to Use

- You have an approved implementation plan with slices and known unknowns, **or** a
  standalone task record that is itself the whole unit of work (`ready`, or `open` and
  refinable — see the standalone branch below)
- You want to execute in the current session

## Skip Gate

**Don't use subagents when:**
- The plan has ≤2 slices, no unknowns, and the total scope is small (≤100 lines expected)
- You'd spend more time writing prompts and absorbing reports than just building it

In those cases, build it yourself following TDD and verification. Subagent overhead isn't free.

**On a standalone task this gate is an explicit judgment, never an automatic one.** A single
leaf trivially satisfies the ≤2-slices bar, so read literally this gate would fire on every
standalone run and swallow the branch below. The escape stays available — a small
standalone task **MAY be built inline** under the same TDD discipline — but
the standalone branch below is the default, and taking the inline route is a call you
state, not one you drift into.

## The Loop

A plan is a parent `task` record whose slices are child `task` records wired with `parent`
(containment) and `depends-on` (ordering) edges. Walk the graph in **topological order** and
dispatch **leaf tasks only** — a child task is workable when it is `ready` and every task it
`depends-on` is `done`. `lore task graph <parent-name>` renders the containment subtree,
per-task status, and marks the runnable leaves; use it to pick the next task and to see
progress. **Never dispatch the parent task itself** — it is the container and the lifecycle
handle, not a unit of work.

### Determine the task shape

Before walking the loop, determine the task's shape. Run `lore task graph <task-name>`
against the task you were pointed at — it may have no parent, so `<task-name>` is the
task itself — and read both the render and the record's sidecar:

- **Standalone.** The graph renders exactly one line for the task AND the record's
  sidecar carries no `parent` edge. There is no child to descend into — the task is the
  whole unit of work. See the standalone branch below.
- **Parent-with-children.** The graph renders more than one line. Proceed with the Loop
  exactly as written below (steps 1-6) — unchanged. **One check first:** if the root you
  rendered itself carries a `parent` edge, you rooted the run at a sub-plan, not at the plan —
  confirm the intended root with the operator (or re-root at the top-level parent) before
  walking it.
- **Ambiguous.** A single-line render **with** a `parent` edge present matches neither
  case above and is never classified silently. Disambiguate first —
  **resolve the `parent` value** (`lore record show task/<parent-value> --vault <elected-vault>`), because the
  two causes have opposite remediations:
  - **It resolves to a real task** — the ordinary cause: you rooted the run at a child
    slice of a live plan, not at the plan. Tell the operator to
    re-root the run at that parent and stop. The graph is healthy; the entry point was
    wrong. Never fall through to the standalone branch — a child slice is not standalone.
  - **It does not resolve** — now the edge is the suspect (a `parent` value that wasn't
    passed as a bare task name silently renders as a detached node; see
    [[lesson/lore-task-graph-parent-depends-on-require-bare-task-names]]).
    Stop and report the suspected mis-wired parent edge, citing that lesson.

**Standalone branch:**

**The task record is the intent document.** There is no plan, so wherever a step or
phase below asks for the *plan path*, a standalone run passes the standalone task record
itself — its captured prose is the why and its `**Delivers:**` / `**Test contract:**` /
`**Files:**` payload is the what. Pass a linked spec alongside it when the task names
one; when it names none, the task record is the whole intent input. State that
substitution in the dispatch prompt so the agent isn't left hunting for a plan.

This one rule covers every dispatch on a standalone run: step 3's `executor` dispatch,
step 4's `drift-gate` dispatch, Phase 2's `simplifier` dispatch, and
Phase 3's whole-change correctness-review dispatch.

- **`ready`** — dispatch only when the node carries the `(runnable)` marker. A
  standalone node rendered without the marker has an unmet `depends-on` edge — report it
  and stop rather than guessing. When runnable, first re-run `../_shared/refine.md`'s
  citation-resolution gate against the task's payload: its verdict was
  stamped at promotion time, and commits landing since can slide a cited line onto
  different-but-existing content. A citation that no longer resolves is a gap again —
  stop and report rather than dispatching against it. Then treat the task itself as
  the one slice: run step 3 (dispatch `executor`) and step 4 (review, scaled to the
  size table) below against it, then skip straight to
  [After All Slices](#after-all-slices) — there is no next leaf to pick.
- **`open`** — run the `../_shared/refine.md` procedure inline. Pass `--interactive` only
  when execute itself has a human channel to a live operator right now; otherwise run it
  unattended. Refine promotes cleanly → report the promotion first — the fields
  filled, any folded-in scope delta, any judgment call made — then proceed as the
  `ready` case above; the promote path is the one with no human between refine and
  an `executor` dispatch, so its report must not be skipped. Refine
  escalates (writes `## Refine — unresolved`) or routes to `/craft:plan` /
  `/craft:brainstorm` → stop and report the refine outcome; do not dispatch an executor
  against a task that did not promote.
- **`blocked`** — report the blocking condition recorded on the task and stop. `blocked`
  encodes an external condition execute can neither observe nor clear.
- **`in-progress`** — a run already started against this task. Resume rather than
  re-dispatching from the top, and read `## End Phases` to decide *where* — it exists from the
  first executor dispatch, so its presence proves nothing about how far the run got:
  - **No ticked phase line** (only the dispatch-count note, or nothing) — the build itself
    may be incomplete, so re-enter the Loop: verify the working tree and the last commit
    against the task's payload, then re-dispatch `executor` for whatever is missing —
    the dispatch count continues from what the notes record, it never restarts at zero.
    Enter the end pipeline only once the build is complete.
  - **At least one ticked phase line** — the build is complete and the end pipeline is
    underway. Re-enter it at the first unticked phase line.

  Either way the clean-working-tree precondition in
  [Phase progress and resumability](#phase-progress-and-resumability) applies before any
  re-dispatch.
- **`done`, `dropped`, `superseded`** — terminal. Report there is nothing to do and stop.

**Status walk.** The standalone task is its own lifecycle handle, so it walks the status
a parent otherwise would — there is no second record to flip. Refine's promotion takes it
`open → ready` (the `open` case above). Then claim it at the **first executor dispatch**
exactly as [Claiming the run](#claiming-the-run-at-first-dispatch) prescribes for a parent —
status and branch label in one command
(`lore record update task/<name> --vault <elected-vault> --status in-progress --label craft/branch=<bare-branch>`),
so crash-resume can find the branch on a standalone run too. Phase 6 takes it
`in-progress → done`, where "close the parent" means close the task itself.

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
`lore record update task/<name> --vault <elected-vault> --unset-label craft/push`.

Otherwise, if the task's workspace no longer exists, reconcile before resuming: the task is
resumed when its branch is recoverable (check out the branch named by `craft/branch` and
continue from the claim in [Claiming the run](#claiming-the-run-at-first-dispatch) onward)
or released back to `ready` when it isn't — `lore record update task/<name> --vault <elected-vault> --status ready`.
Whichever path is taken, append a one-line breadcrumb to the task body —
`reconciled: resumed from <branch>` / `reconciled: released to ready` — with
`lore record update task/<name> --vault <elected-vault> --diff`, piping a unified diff whose only hunk adds that
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
`lore record update task/<parent-name> --vault <elected-vault> --status in-progress --label craft/branch=<bare-branch>`
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
- **INVALIDATED:** pause, report to user, reassess. The design may need to change. Do NOT proceed to build — see [Handling Assumption-Prover Status](#handling-assumption-prover-status). **If the run ends here:** write `blocked` on the plan — `lore record update task/<parent-name> --vault <elected-vault> --status blocked --diff`, piping a unified diff that **appends** the blocked note (bare stdin is a full-body replace and would destroy the record) — plus the [Phase 5](#phase-5-flow-out) scrub and, if commits exist, the [Phase 6](#phase-6-close-and-completion-report) blocked-path push and its standalone `craft/branch` write; body-content contract and full rules in `../_shared/status-ownership.md`.
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

- **Task status.** Set the child task you just built to `done`: `lore record update task/<name> --vault <elected-vault> --status done`. Advancing a task off `ready` is what makes its dependents runnable. A child's `done` here is bookkeeping only — committed on the task branch; the push guarantee that makes `done` mean "committed and pushed" attaches to the run's close (see [Phase 6](#phase-6-close-and-completion-report) and `../_shared/status-ownership.md`), not this step.
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

- The **plan-path substitution** stated once in the Loop's standalone branch above
  governs this pipeline too — Phase 2 and Phase 3 name a plan path in their dispatch
  lists, and a standalone run passes the task record there.
- **Phase 5 works the task's own `## Flow-out` checklist — after making sure it has one.**
  Refine writes that checklist when it promotes a task to `ready`, but a standalone `ready`
  task can arrive from elsewhere (a `/craft:polish` brief, a hand-captured record) carrying
  no such section, and the Red Flag below refuses to close a task without a ticked one. So
  Phase 5 checks first: if the task body carries no `## Flow-out`, then
  append the three items from `${CLAUDE_PLUGIN_ROOT}/templates/plan.md` via `lore record update`
  — the same checklist a planned parent carries — before working it.
- Phase 6 closes the **task itself**, per the status walk above — "close the parent"
  reads as "close the standalone task" on this path.
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

On a match, do **not** push that repo. Take the blocked path instead — `lore record update task/<parent-name> --vault <elected-vault> --status blocked --diff`, piping a unified diff that **appends** the blocked note — and name the remediation in the report: rotate the credential, then rewrite history before attempting the push again.

**This scan gates every push, the blocked path's included.** Each escalation site's "(if commits exist)" push routes through this phase, so it inherits the scan: a run blocked *because* the scan hit stays unpushed until the credential is rotated and the history rewritten. Going `blocked` never licenses shipping the flagged commits.

**The blocked path pushes; it never closes.** Its `craft/branch` write is a standalone command against whichever record just took `blocked` — `lore record update task/<name> --vault <elected-vault> --label craft/branch=<bare-branch>`, or `task/<parent-name>` when the plan is what blocked — **not** the close command below, which also writes `--status done`. A slice-level block is never licence to close the parent.

For every repo that clears the scan, push with `git push --set-upstream origin HEAD` (verbatim — a bare `git push` never converges without an upstream). Push is idempotent: an already-up-to-date branch counts as success, so retrying after a failed status write is safe — the status write is the completion signal, the push is its precondition.

On push failure (auth, no remote, rejection) at close: the task stays `in-progress` — the honest state — and the session labels **the run's task record**, `task/<parent-name>` on this pathway: `lore record update task/<parent-name> --vault <elected-vault> --label craft/push=failed`. That record is the one holding `in-progress` and the one resume logic reads; a child slice's record is bookkeeping, not the run's lifecycle handle, so labelling it would leave the guard where nothing looks for it.

**A blocked-path push failure gets the same label and no status reversion.** The record keeps the `blocked` just written for it — it does *not* revert to `in-progress` — and gains `craft/push=failed` alongside: `lore record update task/<name> --vault <elected-vault> --label craft/push=failed` (`task/<parent-name>` when the plan is what blocked). The label, not the status, is what tells resume logic the commits are un-pushable.

Either way the completion report names the failure **and the remediation**, distinguishing a non-fast-forward rejection (needs reconciliation with the remote, not credentials) from an auth/no-remote failure (needs credentials or a configured remote, not reconciliation). The remediation is not finished when the push finally lands: nothing clears the guard automatically, so settling the push by hand also means `lore record update task/<name> --vault <elected-vault> --unset-label craft/push` — a stale guard makes every later resume skip-and-report. Run any raw git stderr through the credential-pattern scrub before it enters report or record text.

**Close the run.** With every child terminal, the flow-out checklist ticked, and every repo's push settled, set the parent `done`: `lore record update task/<parent-name> --vault <elected-vault> --status done`, re-asserting `--label craft/branch=<bare-branch>` at close. The completion guard refuses this while any child is non-terminal (it names them); a parent closed without a `## Flow-out` section gets a non-blocking flow-out reminder — treat that reminder as a sign the ritual above was skipped, not as a nuisance.

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

- Executor returns `BLOCKED` with unclear cause → dispatch `troubleshooter` (Opus/high) to diagnose before re-dispatching the executor. **If the run ends here:** write `blocked` on the slice — `lore record update task/<name> --vault <elected-vault> --status blocked --diff`, piping a unified diff that **appends** the blocked note (bare stdin is a full-body replace and would destroy the record) — plus the [Phase 5](#phase-5-flow-out) scrub and, if commits exist, the [Phase 6](#phase-6-close-and-completion-report) blocked-path push and its standalone `craft/branch` write; body-content contract and full rules in `../_shared/status-ownership.md`.
- Executor returns `DONE_WITH_CONCERNS` repeatedly on the same slice → re-dispatch with `model: "opus"` or break the slice smaller.
- Assumption-prover returns `NEEDS_CONTEXT` → it's not the model, it's the prompt. Give it more context and re-dispatch at the same tier.

**Why not Opus everywhere:** Opus is the most capable but also the slowest and most expensive. Sonnet is more than enough for mechanical TDD work. Reserve Opus for reasoning-heavy roles (review, troubleshooting, architecture) where fresh eyes matter.

## Handling Assumption-Prover Status

**VALIDATED:** Proceed to build the slice.

**INVALIDATED:** Do NOT build. Report to user with the evidence. Options:
1. **Minor adjustment** — the design holds, just one child task changes. Update the affected child task record (`lore record update task/<name> --vault <elected-vault> …`), note what changed and why — running that note through the [Phase 5](#phase-5-flow-out) credential scrub, since it lands in a record body — and continue.
2. **Design change** — the invalidation affects multiple child tasks or the architecture. Re-enter planning: dispatch the `planner` subagent (isolated, Opus) or invoke the `planning` skill inline. Do NOT use `EnterPlanMode` — plan mode blocks writes to the plan vault.
3. **Drop the task** — the feature doesn't need this part. Reshape the child task record to `superseded` (or `dropped`) via `lore record update`, note why, continue with remaining tasks.

**If the run ends here** (none of the above resolves it in-session): write `blocked` on the plan — `lore record update task/<parent-name> --vault <elected-vault> --status blocked --diff`, piping a unified diff that **appends** the blocked note (bare stdin is a full-body replace and would destroy the record) — plus the [Phase 5](#phase-5-flow-out) scrub and, if commits exist, the [Phase 6](#phase-6-close-and-completion-report) blocked-path push and its standalone `craft/branch` write; body-content contract and full rules in `../_shared/status-ownership.md`.

If the INVALIDATED result is surprising (behavior you thought was standard turns out to differ), that may also be a `troubleshooter` question: dispatch it to figure out *why* the assumption was wrong before reshaping the plan.

**NEEDS_CONTEXT:** Provide missing context and re-dispatch.

**BLOCKED:** Assess — provide more context, use a more capable model, or escalate to user. **If the run ends here:** write `blocked` on the slice — `lore record update task/<name> --vault <elected-vault> --status blocked --diff`, piping a unified diff that **appends** the blocked note (bare stdin is a full-body replace and would destroy the record) — plus the [Phase 5](#phase-5-flow-out) scrub and, if commits exist, the [Phase 6](#phase-6-close-and-completion-report) blocked-path push and its standalone `craft/branch` write; body-content contract and full rules in `../_shared/status-ownership.md`.

## Handling Executor Status

**DONE:** Proceed to review.

**DONE_WITH_CONCERNS:** Read concerns. If about correctness/scope, address before review. If observations, note and proceed.

**NEEDS_CONTEXT:** Provide missing context and re-dispatch.

**BLOCKED:** Assess the blocker:
1. Context problem → provide more context, re-dispatch
2. Needs more reasoning → re-dispatch with `model: "opus"`
3. Slice too large → break into smaller pieces
4. Plan is wrong → escalate to user. **If the run ends here:** write `blocked` on the plan — `lore record update task/<parent-name> --vault <elected-vault> --status blocked --diff`, piping a unified diff that **appends** the blocked note (bare stdin is a full-body replace and would destroy the record) — plus the [Phase 5](#phase-5-flow-out) scrub and, if commits exist, the [Phase 6](#phase-6-close-and-completion-report) blocked-path push and its standalone `craft/branch` write; body-content contract and full rules in `../_shared/status-ownership.md`.
5. Cause unclear → dispatch `troubleshooter` to diagnose before re-dispatching the executor. Don't keep re-dispatching the same prompt hoping for a different outcome.

**If the run ends here** (1–3 or 5 above didn't resolve it): write `blocked` on the slice — `lore record update task/<name> --vault <elected-vault> --status blocked --diff`, piping a unified diff that **appends** the blocked note (bare stdin is a full-body replace and would destroy the record) — plus the [Phase 5](#phase-5-flow-out) scrub and, if commits exist, the [Phase 6](#phase-6-close-and-completion-report) blocked-path push and its standalone `craft/branch` write; body-content contract and full rules in `../_shared/status-ownership.md`.

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
