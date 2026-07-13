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

For each runnable child task (a slice):

### 1. Does this slice have an unresolved unknown?

**Yes → dispatch `assumption-prover`.**

The agent expects: plan path, the unknown (specific and restated), why it matters (which slice is blocked), working directory.

It returns: VALIDATED / INVALIDATED / NEEDS_CONTEXT / BLOCKED, plus evidence, test files to clean up, and surprises.

**No → skip to step 3**

### 2. Absorb findings

- **VALIDATED:** update the plan, check off the unknown. Carry the **test files to clean up** from the prover's report into the executor dispatch so it removes them after building proper tests.
- **INVALIDATED:** pause, report to user, reassess. The design may need to change. Do NOT proceed to build — see [Handling Assumption-Prover Status](#handling-assumption-prover-status).
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

Absorb the reviewer's verdict — `PASS` | `DRIFT` | `BLOCKED` — plus its findings into your working context. If it emits a `Security-surface:` line, carry it forward into a running list for the whole-change security trigger (a later After All Slices phase) — do not act on it per-slice.

Quality, style, and design review are explicitly out of scope for `drift-gate` — that's deferred to the whole-change phases (a later addition to After All Slices), not dropped entirely.

### 5. Update the task graph

After each child task completes (or each unknown resolves), record the state on the graph — the task graph is the source of truth for what's done and what's left, so the run stays resumable if context breaks (handoff, new session):

- **Task status.** Set the child task you just built to `done`: `lore record update task/<name> --status done`. Advancing a task off `ready` is what makes its dependents runnable.
- **Parent lifecycle.** After the **first** child task lands, flip the parent `ready → in-progress` (`lore record update task/<parent-name> --status in-progress`; bump `updated:` to today). This keeps the task graph honest — a plan with code shipped against it should not still read `ready`. Skip if the parent is already `in-progress`, `done`, `dropped`, or `superseded`.
- **Unknowns.** Check off resolved unknowns in the parent's Known Unknowns block; add any new unknown discovered during the slice, noting which child task it blocks.
- **Design changes.** Note any design change a finding forced — in the parent body, and by re-shaping child tasks per the split/append ritual below.

**Per-cycle working set:** the controller's working set is the current child task plus the parent's Known Unknowns block. The controller does not re-read the whole graph each cycle — it updates incrementally and re-reads only `lore task graph <parent-name>` to pick the next runnable leaf.

(The parent's final `in-progress → done` flip is the flow-out completion gate — see [After All Slices](#after-all-slices). It does not happen here.)

### 6. Next task

Re-run `lore task graph <parent-name>`, pick the next runnable leaf (topological order), and repeat from step 1. When no runnable leaf remains and every child is terminal, go to [After All Slices](#after-all-slices).

### Splitting and appending tasks

The graph is not frozen at plan time — reshape it as reality demands:

- **Split** — a slice turns out too large or has an internal ordering. Create the new child tasks (`lore record create --kind task … --parent <parent-name>`), wire `--depends-on` edges between them and to the original's dependents, then set the original task `superseded` (or `dropped`) so it drops out of the runnable set. Note why in the parent body.
- **Append** — new work surfaces mid-execution. Create a fresh child task under the parent with the right `--depends-on` edges; the ordering sequences it automatically.

Both keep the graph — not a prose list — the single source of truth for what's left.

## After All Slices

Every child task is terminal (`done`/`dropped`/`superseded`). Before closing the parent:

1. Run verification — dispatch `test-runner` for each applicable suite (the project's test run and lint/typecheck/CI checks) rather than running inline. Keeps the noisy test output out of your main context and returns a concise pass/fail.
2. **Knowledge flow-out completion ritual (gate).** The parent carries a `## Flow-out` checklist — work it *before* the parent goes `done`, not after:
   - **Update touched area/subsystem profiles** with what actually changed (via the `lore` CLI), so the next agent inherits current ground truth.
   - **Capture prover-validated assumptions** and any decisions / lessons / follow-ups surfaced during the build as **session candidates** (`lore session candidate …`) — they become durable records at flush.
   - **Tick the parent's `## Flow-out` checklist** to reflect what you did.
3. **Close the parent.** With every child terminal and the flow-out checklist ticked, set the parent `done`: `lore record update task/<parent-name> --status done`. The completion guard refuses this while any child is non-terminal (it names them); a parent closed without a `## Flow-out` section gets a non-blocking flow-out reminder — treat that reminder as a sign the ritual above was skipped, not as a nuisance.
4. Report completion to the user and stop. Do **not** automatically invoke `/portage:open` — the user decides when to open a PR.

## Model Selection

Defaults are baked into each agent's frontmatter. Escalate when signals say you should.

| Role | Default | Escalate to |
|------|---------|-------------|
| `assumption-prover` | Sonnet | Sonnet/high if the unknown spans multiple subsystems or needs deeper code exploration |
| `executor` | Sonnet | `model: "opus"` per-dispatch for integration-heavy slices |
| `drift-gate` | Sonnet/high | (already pinned, no override needed) |

**Escalation signals:**

- Executor returns `BLOCKED` with unclear cause → dispatch `troubleshooter` (Opus/high) to diagnose before re-dispatching the executor.
- Executor returns `DONE_WITH_CONCERNS` repeatedly on the same slice → re-dispatch with `model: "opus"` or break the slice smaller.
- Assumption-prover returns `NEEDS_CONTEXT` → it's not the model, it's the prompt. Give it more context and re-dispatch at the same tier.

**Why not Opus everywhere:** Opus is the most capable but also the slowest and most expensive. Sonnet is more than enough for mechanical TDD work. Reserve Opus for reasoning-heavy roles (review, troubleshooting, architecture) where fresh eyes matter.

## Handling Assumption-Prover Status

**VALIDATED:** Proceed to build the slice.

**INVALIDATED:** Do NOT build. Report to user with the evidence. Options:
1. **Minor adjustment** — the design holds, just one child task changes. Update the affected child task record (`lore record update task/<name> …`), note what changed and why, continue.
2. **Design change** — the invalidation affects multiple child tasks or the architecture. Re-enter planning: dispatch the `planner` subagent (isolated, Opus) or invoke the `planning` skill inline. Do NOT use `EnterPlanMode` — plan mode blocks writes to the plan vault.
3. **Drop the task** — the feature doesn't need this part. Reshape the child task record to `superseded` (or `dropped`) via `lore record update`, note why, continue with remaining tasks.

If the INVALIDATED result is surprising (behavior you thought was standard turns out to differ), that may also be a `troubleshooter` question: dispatch it to figure out *why* the assumption was wrong before reshaping the plan.

**NEEDS_CONTEXT:** Provide missing context and re-dispatch.

**BLOCKED:** Assess — provide more context, use a more capable model, or escalate to user.

## Handling Executor Status

**DONE:** Proceed to review.

**DONE_WITH_CONCERNS:** Read concerns. If about correctness/scope, address before review. If observations, note and proceed.

**NEEDS_CONTEXT:** Provide missing context and re-dispatch.

**BLOCKED:** Assess the blocker:
1. Context problem → provide more context, re-dispatch
2. Needs more reasoning → re-dispatch with `model: "opus"`
3. Slice too large → break into smaller pieces
4. Plan is wrong → escalate to user
5. Cause unclear → dispatch `troubleshooter` to diagnose before re-dispatching the executor. Don't keep re-dispatching the same prompt hoping for a different outcome.

## Red Flags

**Never:**
- Build a slice before its unknown is resolved
- Proceed after an invalidated assumption without user input
- Skip review for medium+ changes
- Dispatch multiple *executor* subagents in parallel on the same slice (they'll conflict on the same files). Parallel dispatch is fine when the agents operate on independent scopes — e.g. one checker per repo.
- Dispatch the parent task as if it were a slice — it is the container and lifecycle handle, never a unit of work.
- Close the parent `done` with non-terminal children or an un-ticked `## Flow-out` checklist — the completion guard blocks the former; skipping the latter traps knowledge.
- Ignore subagent questions or surprises
- Start on main/master without explicit user consent
