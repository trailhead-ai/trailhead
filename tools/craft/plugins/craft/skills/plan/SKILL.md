---
name: plan
description: >
  Use when starting a new feature, substantial change, or multi-step task that needs design alignment before code.
  TRIGGER when: user says "feature", "implement", "build", "add support for", "design", "plan", "architecture", or describes a multi-step task.
  DO NOT TRIGGER when: user says "fix", "bug", "debug", "broken", small single-file changes, or tasks with clear intent needing no design.
---

# Plan

Design the whole feature end-to-end, then build it in slices — proving unknowns before building on top of them.

**A plan is a hypothesis, not a contract. It can be invalidated at any point.**

## Skip Gate

**Do NOT use this skill for:**
- Small, well-scoped tasks the user frames as "small update", "fix this", or similar
- Changes scoped to a single file with clear intent
- Bug fixes (debug them directly, not via planning)

If the user has already decided what to do, just do it.

**Inline vs. dispatched:** This skill runs planning inline in the current session — use when you want interactive design alignment with the user. If planning is a step inside an automated workflow, or you want to offload design work to an isolated context, dispatch a planner subagent instead — it runs the full brainstorm → spec → plan arc in an isolated context.

## Process

Do not use `EnterPlanMode`/`ExitPlanMode` — plan mode forces plans into an ephemeral harness file and blocks the plan write described below. Instead, self-enforce the discipline: no implementation code until the user approves the written plan.

### 1. Explore Context

- **Look for an upstream spec first.** If your project uses lore, check for a `status: ready` spec on this topic (`lore search 'kind:spec status:ready'`). If one exists, it defines the *what* and *why* — your job is the *how*. Read it fully (`lore record show spec/<name>`) before proceeding. If none exists and the idea is fuzzy (acceptance criteria unclear, scope ambiguous, UI undecided), stop and route to a brainstorming skill instead.
- Check files, docs, recent commits relevant to the request
- For cross-subsystem features, if a knowledge-synthesis subagent is available (such as `lore:librarian`), dispatch it to get a synthesized view of subsystems, decisions, backlog items, and lessons rather than listing directories yourself. If none is configured, query the vault through the `lore` CLI directly (`lore search`, then `lore record show` — never raw file reads), and note in the plan that the prior-art synthesis pass was skipped.
- **Consult your vault's lessons, if present,** for the touched subsystems before sketching the plan. Active lessons capture mistakes made before with concrete prevention checks — explicitly note in the plan how each relevant lesson's prevention applies (or why it doesn't). This is the same gate as a dropped backlog item, just one level higher: a dropped approach says "don't try X technique"; lessons say "don't repeat Y kind of judgment error."
- For genuinely complex existing code you need to understand before designing, dispatch `researcher` rather than burning your own context on file surveying
- If the request spans multiple independent subsystems, flag it immediately — decompose before designing

### 2. Clarify (1-2 questions max)

- Ask clarifying questions one at a time
- Prefer multiple choice when possible
- Focus on: implementation constraints and unfamiliar dependencies (purpose / success criteria belong in the spec — don't relitigate them here)
- Don't over-ask. If the intent is clear, move to approaches.
- **Bounce-back rule:** if a question would change the spec's objectives, acceptance criteria, or non-goals, stop planning and route back to brainstorming — that work belongs upstream. Task-level uncertainty (library choice, query shape, naming) stays in planning.

### 3. Propose Approaches

- Propose 2-3 approaches with trade-offs
- Lead with your recommendation and why
- YAGNI ruthlessly — remove unnecessary features from all options
- For genuinely gnarly architectural choices (multiple valid paths with large blast-radius differences), consider dispatching `architect` to get an independent recommendation in an isolated context before committing

### 4. Design End-to-End

Present the full design: architecture, components, data flow, key decisions. The goal is a shared understanding of *what* we're building and *how* the pieces fit together. Scale each section to its complexity.

### 5. Research External Dependencies

If the design involves libraries, frameworks, or language features you haven't used in this codebase before:

- **For a quick API reference lookup:** dispatch `doc-finder` — it's fast and returns a URL + minimum excerpt.
- **For deep investigation of library behavior or tradeoffs between competing libraries:** dispatch `researcher` and fold its findings into the design.
- **Otherwise, fetch the official documentation** (library README, API reference, framework guide) from a reputable source before finalizing the design.
- Don't design around assumed APIs — verify them
- Note version-specific behavior if relevant (e.g., a feature added in v3.x)
- If documentation is sparse or contradictory, flag it as a known unknown

This applies to new dependencies AND to unfamiliar parts of existing dependencies.

Findings from this step (library version, API quirks, supported behaviors) feed directly into the plan template's **Given Axioms** block as cited ground truth. If something needs investigation to verify, leave it as a Known Unknown for the assumption-prover instead.

### 6. Identify Known Unknowns

After the design is agreed, explicitly call out assumptions that need to be proven before building on top of them:

- What do we believe to be true but haven't verified? (e.g., "the existing resolver supports cursor-based pagination," "the mobile client handles empty state gracefully")
- Which unknowns are riskiest — highest cost if wrong?
- Which slices depend on which unknowns?

These are the things that, if wrong, would change the design.

### 7. Define Slices

Break the feature into buildable slices. Each slice is a vertical cut of functionality that can be built, tested, and committed independently. Order slices so that:

- Slices with unproven unknowns come first
- Each slice produces something testable
- Later slices build on validated earlier ones

**Every slice follows TDD.** Each slice description must include the test contract — the behaviors to prove with failing tests before writing implementation code. Slices that skip or defer tests are not valid slices.

Slices don't need step-by-step implementation detail. The subagent figures out how to build it. What the plan needs is: what the slice delivers, what files it touches, what unknown (if any) it depends on, and what test behaviors prove the slice works.

### 8. Write the Plan

Persist the plan with `lore record create` (`../_shared/note-storage.md`): render craft's plan body template (`templates/plan.md`), fill in the sections, then pipe the filled body to it — `printf '%s' "$BODY" | lore record create --kind plan --title "<topic>" --status draft`. This stores it as a searchable lore `plan` record, which keeps it linkable from session notes and future planning.

If the `lore` CLI is not on PATH, write the plan to a `plans/` directory in your vault manually, mirroring the template shape below.

**Populate `related-subsystems:` frontmatter** from your vault's subsystem profiles, if present — so the plan is linked to the areas it touches. List every subsystem the plan touches, not just the primary one.

If an upstream spec exists, link the plan to it with `lore record update <plan-id> --related spec=<spec-name>` and advance the spec's status `ready → planned` (`lore record update <spec-id> --status planned`) after the plan is written. Do **not** create a new design spec — the upstream spec is the canonical "what / why" doc; the plan is the "how".

The plan body template (`templates/plan.md`) carries these canonical sections — fill each in:

- **Goal** — one or two sentences
- **Architecture** — 2-3 sentences about the approach
- **Given Axioms** — the ground truth this plan rests on. Each axiom must be either (a) verifiable at a file:line citation, (b) traced to a recorded decision/ADR, or (c) a constraint stated by the user. If you'd need to investigate to know it's true, it belongs in **Known Unknowns**, not here.
- **Known Unknowns** — checkbox list; each notes which slice it blocks.
- **Slices** — each slice carries Delivers / Test contract / Files, plus "Unknown to resolve first" and "Depends on" where applicable.

Leave the `## Council Review` section for Step 8.5 to append — do not pre-fill it.

### 8.5. Council Review (mandatory)

After the plan is written and before presenting it for approval, dispatch a council review. The council members review the plan + its linked spec in parallel; the main session synthesizes their findings and gates approval on disposition of any Critical findings.

**Membership is defined in `_shared/council.md`** — the single source of truth for who the council is (`builder` / `breaker` / `attacker` / `advocate`) and the dispatch contract. Read it; do not hardcode the roster here. Planning dispatches the council **directly** off that shared list — it does **not** call the `/craft:consult` skill (a skill→skill chain is unreliable). `consult` is the standalone-invocable form of this same panel for questions outside the planning flow.

This step is mandatory on every plan. There is no skip flag — calibration is tuned via the per-lens Critical bars in `_shared/council.md`, not via per-invocation opt-outs.

**Dispatch:** per `_shared/council.md`, make four parallel `Agent` tool calls — one each to `builder`, `breaker`, `attacker`, `advocate` — in a single message so they run concurrently. Use the **prompt template, per-lens Critical bars, and synthesis rules in `_shared/council.md`** — do not re-inline them here. Fill the template's substitution tokens BEFORE sending each member its prompt (never ship a literal `<token>`):
- the context-pointer line → these two lines (substitute `<spec-path>` with the plan's linked spec absolute path; if the plan has no `related-spec` frontmatter, replace the whole `Spec:` line with `Spec: none — review against the plan's own Goal and Architecture blocks`):
  ```text
  Review the implementation plan and its linked spec against your lens.
  Plan: <plan-path>
  Spec: <spec-path>
  ```
- `<lens-critical-bars>` → the matching block from "Per-lens Critical bars" in `_shared/council.md`
- `<cross-cutting>` → this plan-specific extra Critical block:
  ```text

  Cross-cutting Critical you may also raise (any lens):
  - Spec drift: plan's slices, summed, don't satisfy spec's acceptance criteria
  - Hidden scope expansion: plan touches a subsystem the spec didn't claim
  - Reversibility unnamed: plan deploys something hard to roll back without naming rollback path
  ```

Then synthesize per `_shared/council.md` (de-duplicate by issue, auto-downgrade speculative Criticals, present grouped by severity). When auto-downgrading, record the demotion in the persisted `## Council Review` section under Important with a `(downgraded from Critical: <reason>)` parenthetical so calibration audits can detect demote-heavy synthesizers.

**Disposition (required for every Critical):**

For each Critical finding, the user assigns one of:
- `resolved` — user edits the plan inline to address the finding
- `bounced-back-to-spec` — finding changes spec objectives / AC / non-goals; stop planning, route back to brainstorming
- `accepted-as-risk: <reason>` — explicit acceptance, recorded for audit
- `disputed: <reason>` — user disagrees with the finding; recorded for audit

Important and Minor findings do NOT require dispositions — they are logged for the audit trail only.

**Persistence:** append a `## Council Review` section to the plan file capturing all findings and the disposition for each Critical. Each Critical disposition is exactly one of `resolved` / `bounced-back-to-spec` / `accepted-as-risk: <reason>` / `disputed: <reason>`. Mirror this populated shape:

```markdown
## Council Review

*Reviewed at:* 2026-05-22T19:42:11Z
*Members dispatched:* builder, breaker, attacker, advocate

*Critical:*
- Slice 2 producer contract isn't tested but Slice 3 consumer depends on it (raised by: Builder) — *Disposition:* `resolved`
- New admin endpoint has no named authz check (raised by: Security) — *Disposition:* `accepted-as-risk: endpoint already behind admin scope guard at router:142; explicit per-endpoint check deferred to next iteration`

*Important:*
- A destructive backfill in Slice 4 runs outside a replayable console (raised by: Reliability)
- Loading state copy reads as dev jargon (raised by: Advocate) (downgraded from Critical: speculative — no concrete user moment named)

*Minor:*
- Slice naming inconsistent between "Validate" and "Verify" (raised by: Builder)
```

If no Critical findings surfaced, the section still gets appended — record an empty Critical list explicitly (e.g. `*Critical:* none`) so future audits can distinguish "zero findings" from "review skipped."

**Re-review:** no automatic re-review after the user resolves Critical findings inline. The user attests the fix is in (or that the finding is accepted/disputed), and planning proceeds. This keeps mandatory-review cost bounded; if first-pass calibration is wrong, tune the per-lens bars in `_shared/council.md` rather than adding iteration cycles.

**Hard-floor gate:** the "reply `build` to hand off" prompt in Step 9 must NOT be printed until every Critical finding has a disposition. Important and Minor findings do not block.

### 9. Present for Approval

Share the plan path and a short summary, then wait for explicit user approval before writing any implementation code. Do **not** call `ExitPlanMode` — this skill runs outside plan mode so the plan can be written directly into the vault.

**Before printing the handoff prompt, confirm every Critical finding from Step 8.5 has a disposition recorded in the plan's `## Council Review` section.** If any Critical is undisposed, do not print the handoff prompt — return to disposition gathering with the user.

End the presentation with an explicit handoff prompt so the trigger is unambiguous, e.g.:

> "Plan is written to your vault. Reply **build** to hand off to `/craft:execute` and start building slice by slice, or call out anything that needs adjustment first."

The continuation verb (`build`, `start`, `go`, `ship it`) is what pulls in the `/craft:execute` skill — don't rely on implicit continuation. Use a verb here rather than the bare skill name so the trigger word stays distinct from the `/craft:execute` command itself.

## Key Principles

- **Design the whole, build in slices** — understand the full picture, then prove and build incrementally
- **Tests before code, always** — every slice follows TDD. The plan defines *what* to test; a test-driven-development skill enforces *how*. No slice ships without a failing test first.
- **Prove before building** — if a slice depends on an unknown, resolve the unknown first
- **Plans are disposable** — if an unknown is invalidated, the plan changes
- **State unknowns explicitly** — so they can be caught early
- **Flag surprises immediately** — unexpected behavior during implementation may invalidate the direction
