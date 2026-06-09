---
name: subagent-driven-development
description: >
  Use when executing an approved implementation plan slice-by-slice, dispatching `sdd-assumption-prover`
  and `sdd-implementer` subagents for each slice rather than building inline. The controller (you)
  orchestrates; subagents do the work.
  TRIGGER when: user says "execute", "execute the plan", "start building", "let's build", "build it",
  "implement this", "run the plan", "work the slices", "start the slices", "go" (following plan approval),
  "ship it", or picks up a plan with unfinished slices via `/pickup`. Also triggers as the natural
  handoff after `/planning` when the user approves the written plan.
  DO NOT TRIGGER when: no plan exists yet (use `/planning` or `planner` first), the plan has ≤2 slices
  with no unknowns and small scope (just build it yourself), or the user is debugging rather than
  executing (use `systematic-debugging`).
---

# Subagent-Driven Development

Execute a plan slice-by-slice. For each slice, resolve unknowns first, then build.

**Three subagent roles** — all dedicated agents, no inline prompt templates:

| Role | Agent | Purpose |
|------|-------|---------|
| Resolve unknowns | `sdd-assumption-prover` | Writes a TDD test that proves or disproves an assumption |
| Build slices | `sdd-implementer` | Writes tests first, implements, self-reviews, commits |
| Review work | `code-reviewer` | Spec compliance + code quality in one pass |

The controller decides which to dispatch and absorbs findings between iterations.

## When to Use

- You have an approved implementation plan with slices and known unknowns
- You want to execute in the current session

## Skip Gate

**Don't use subagents when:**
- The plan has ≤2 slices, no unknowns, and the total scope is small (≤100 lines expected)
- You'd spend more time writing prompts and absorbing reports than just building it

In those cases, build it yourself following TDD and verification. Subagent overhead isn't free.

## Pre-Loop: Feature Flag Setup

Before the first slice, read the plan's `Feature Flag` field.

- **Flag declared:** wire up the flag *now* (before slice 1) — provider SDK detection, flag creation, and any first-touch wire-up. **Feature-flag provider (extension point — `feature_flags`):** if a feature-flag provider is configured in your environment, dispatch its configuration skill now, passing the flag key, default state, and the touchpoint list from the plan; once it returns, proceed to slice 1. **If no feature-flag provider configured — flag setup skipped** (see the extend guide in `docs/DEGRADATION.md`); proceed to slice 1.
- **`n/a`:** skip — no flag work this loop.
- **Field missing:** stop. The plan is non-conformant. Bounce back to `planning` (or `brainstorm` if the spec is also missing the rollout decision). Do not invent a flag and do not proceed flagless if the plan should have one.

When the flag is declared, every slice that touches the gated path **must** include test cases for both flag states (on and off) in its test contract. The `sdd-implementer` is responsible for executing these via TDD; the controller verifies both states are covered before marking the slice DONE. Treat a slice that only tests the on-path as incomplete — bounce it back.

## The Loop

### Issue tracker — advance to "in progress"

Before dispatching the first slice (whether assumption-prover or implementer for slice 1), advance the work item's status if an issue tracker is wired up. **Issue tracker (extension point — `issue_tracker`):** if an issue tracker is configured in your environment, advance the corresponding ticket to the in-progress status (e.g. "Code In Progress" or equivalent). **If no issue tracker configured — status transitions skipped**; proceed directly to slice 1.

For each slice in the plan:

### 1. Does this slice have an unresolved unknown?

**Yes → dispatch `sdd-assumption-prover`.**

The agent expects: plan path, the unknown (specific and restated), why it matters (which slice is blocked), working directory.

It returns: VALIDATED / INVALIDATED / NEEDS_CONTEXT / BLOCKED, plus evidence, test files to clean up, and surprises.

**No → skip to step 3**

### 2. Absorb findings

- **VALIDATED:** update the plan, check off the unknown. Carry the **test files to clean up** from the prover's report into the implementer dispatch so it removes them after building proper tests.
- **INVALIDATED:** pause, report to user, reassess. The design may need to change. Do NOT proceed to build — see [Handling Assumption-Prover Status](#handling-assumption-prover-status).
- **Surprises:** if the prover discovered new unknowns, add them to the plan. Decide whether they block the current slice or a future one.

### 3. Dispatch `sdd-implementer`

The agent expects:
- Plan path and slice number/name
- Proven unknowns summary (or "None")
- Assumption-prover tests to clean up (or "None")
- Working directory

The implementer figures out implementation steps — don't over-specify the *how*. Specify the *what*.

Default model is Sonnet. Override per-dispatch when needed:
- `model: "opus"` for integration-heavy slices (3-5 files, cross-module coordination)
- Re-dispatch with Opus if a Sonnet attempt returns BLOCKED with unclear cause and `troubleshooter` confirms the issue is reasoning capacity

Returns: DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED. See [Handling Implementer Status](#handling-implementer-status).

### 4. Review (scaled to change size)

| Change Size | Review Approach |
|-------------|----------------|
| **Small** (≤30 lines, 1-2 files) | Skip formal review. Review inline or one quick check. |
| **Medium** (30-200 lines, 3-5 files) | Dispatch `code-reviewer` for combined spec + quality pass. |
| **Large** (200+ lines or 5+ files) | Dispatch `code-reviewer` twice: once asking for spec compliance focus, once for code quality. Same agent, different framing. |

When dispatching `code-reviewer`, give it: plan path + slice number, the implementer's status report (so it can verify the claim), and base/head SHAs for the diff.

### 5. Update the plan file

After each slice completes (or each unknown resolves), update the plan file the caller provides (the `lore` plan, or wherever your plans live):
- Check off resolved unknowns
- Mark completed slices
- Add any new unknowns discovered during the slice
- Note any design changes forced by findings

This is essential for session continuity — if context breaks (handoff, new session), the plan file is the source of truth for what's done and what's left.

**After the first slice lands:** if the plan's frontmatter `status` is `draft`, flip it to `in-progress` and bump `updated:` to today. This keeps the plans index honest — a plan with code shipped against it should not still show `draft`. Skip if `status` is already `in-progress`, `active`, `complete`, or `superseded`.

(The matching `complete` flip on final-slice-shipped happens once the work lands — your merge/completion flow handles the final plan-status flip.)

### 6. Next slice

Move to the next slice in the plan. Repeat from step 1.

## After All Slices

### Issue tracker — advance to "complete"

Before running verification, advance the work item's status if an issue tracker is wired up. **Issue tracker (extension point — `issue_tracker`):** if an issue tracker is configured in your environment, advance the corresponding ticket to the complete status (e.g. "Code Complete" or equivalent). **If no issue tracker configured — status transitions skipped**; proceed directly to verification.

1. Run verification — dispatch `test-runner` for each applicable suite (the project's test run and lint/typecheck/CI checks) rather than running inline. Keeps the noisy test output out of your main context and returns a concise pass/fail.
2. Report completion to the user and stop. Do **not** automatically invoke `/create-pr` — the user decides when to open a PR.

## Model Selection

Defaults are baked into each agent's frontmatter. Escalate when signals say you should.

| Role | Default | Escalate to |
|------|---------|-------------|
| `sdd-assumption-prover` | Sonnet | Sonnet/high if the unknown spans multiple subsystems or needs deeper code exploration |
| `sdd-implementer` | Sonnet | `model: "opus"` per-dispatch for integration-heavy slices |
| `code-reviewer` | Opus/high | (already pinned, no override needed) |

**Escalation signals:**

- Implementer returns `BLOCKED` with unclear cause → dispatch `troubleshooter` (Opus/high) to diagnose before re-dispatching the implementer.
- Implementer returns `DONE_WITH_CONCERNS` repeatedly on the same slice → re-dispatch with `model: "opus"` or break the slice smaller.
- Assumption-prover returns `NEEDS_CONTEXT` → it's not the model, it's the prompt. Give it more context and re-dispatch at the same tier.

**Why not Opus everywhere:** Opus is the most capable but also the slowest and most expensive. Sonnet is more than enough for mechanical TDD work. Reserve Opus for reasoning-heavy roles (review, troubleshooting, architecture) where fresh eyes matter.

## Handling Assumption-Prover Status

**VALIDATED:** Proceed to build the slice.

**INVALIDATED:** Do NOT build. Report to user with the evidence. Options:
1. **Minor adjustment** — the design holds, just one slice changes. Edit the plan file inline, note what changed and why, continue.
2. **Design change** — the invalidation affects multiple slices or the architecture. Re-enter planning: dispatch the `planner` subagent (isolated, Opus) or invoke the `planning` skill inline. Do NOT use `EnterPlanMode` — plan mode blocks writes to the plan vault.
3. **Drop the slice** — the feature doesn't need this part. Remove it from the plan, note why, continue with remaining slices.

If the INVALIDATED result is surprising (behavior you thought was standard turns out to differ), that may also be a `troubleshooter` question: dispatch it to figure out *why* the assumption was wrong before reshaping the plan.

**NEEDS_CONTEXT:** Provide missing context and re-dispatch.

**BLOCKED:** Assess — provide more context, use a more capable model, or escalate to user.

## Handling Implementer Status

**DONE:** Proceed to review.

**DONE_WITH_CONCERNS:** Read concerns. If about correctness/scope, address before review. If observations, note and proceed.

**NEEDS_CONTEXT:** Provide missing context and re-dispatch.

**BLOCKED:** Assess the blocker:
1. Context problem → provide more context, re-dispatch
2. Needs more reasoning → re-dispatch with `model: "opus"`
3. Slice too large → break into smaller pieces
4. Plan is wrong → escalate to user
5. Cause unclear → dispatch `troubleshooter` to diagnose before re-dispatching the implementer. Don't keep re-dispatching the same prompt hoping for a different outcome.

## Red Flags

**Never:**
- Build a slice before its unknown is resolved
- Proceed after an invalidated assumption without user input
- Skip review for medium+ changes
- Dispatch multiple *implementer* subagents in parallel on the same slice (they'll conflict on the same files). Parallel dispatch is fine when the agents operate on independent scopes — e.g. one checker per repo.
- Ignore subagent questions or surprises
- Start on main/master without explicit user consent
