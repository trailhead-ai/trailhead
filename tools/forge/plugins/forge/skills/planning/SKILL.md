---
name: planning
description: >
  Use when starting a new feature, substantial change, or multi-step task that needs design alignment before code.
  TRIGGER when: user says "feature", "implement", "build", "add support for", "design", "plan", "architecture", or describes a multi-step task.
  DO NOT TRIGGER when: user says "fix", "bug", "debug", "broken", small single-file changes, or tasks with clear intent needing no design.
---

# Planning

Design the whole feature end-to-end, then build it in slices — proving unknowns before building on top of them.

**A plan is a hypothesis, not a contract. It can be invalidated at any point.**

## Skip Gate

**Do NOT use this skill for:**
- Small, well-scoped tasks the user frames as "small update", "fix this", or similar
- Changes scoped to a single file with clear intent
- Bug fixes (use a systematic-debugging skill instead)

If the user has already decided what to do, just do it.

**Inline vs. dispatched:** This skill runs planning inline in the current session — use when you want interactive design alignment with the user. If planning is a step inside an automated workflow, or you want to offload design work to an isolated context, dispatch a planner subagent instead — it runs the full brainstorm → spec → plan arc in an isolated context.

## Process

Do not use `EnterPlanMode`/`ExitPlanMode` — plan mode forces plans into an ephemeral harness file and blocks the plan write described below. Instead, self-enforce the discipline: no implementation code until the user approves the written plan.

### 1. Explore Context

- **Look for an upstream spec first.** Check your vault's `specs/` for a `status: ready` spec on this topic. If one exists, it defines the *what* and *why* — your job is the *how*. Read it fully before proceeding. If none exists and the idea is fuzzy (acceptance criteria unclear, scope ambiguous, UI undecided), stop and route to a brainstorming skill instead.
- Check files, docs, recent commits relevant to the request
- For cross-subsystem features, if a knowledge-synthesis subagent is available (such as `lore:lore-librarian`), dispatch it to get a synthesized view of subsystems, decisions, dead-ends, lessons, and deferred items rather than listing directories yourself. If none is configured, read the relevant vault notes directly — and note in the plan that the prior-art synthesis pass was skipped.
- **Consult your vault's lessons, if present,** for the touched subsystems before sketching the plan. Active lessons capture mistakes made before with concrete prevention checks — explicitly note in the plan how each relevant lesson's prevention applies (or why it doesn't). This is the same gate as dead-ends, just one level higher: dead-ends say "don't try X technique"; lessons say "don't repeat Y kind of judgment error."
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

### 7. Map Feature Flag Touchpoints (if spec declared a flag)

Read the spec's `Rollout & Gating` section.

- **Flag declared:** the plan must name the touchpoints — every call site, route, component, or query the flag will gate. Identify the evaluation point(s) (server-side preferred; client-side only if needed for UI flicker concerns) and the default-off behavior. Do NOT design the SDK setup or wire-up in detail here — that is the flag provider's job at execution time. The plan needs *where the flag goes*, not *how the provider evaluates it*. Carry the flag key, default state, and touchpoint list into the plan template's `Rollout & Gating` section.
- **n/a:** record the spec's reason verbatim under `Rollout & Gating: n/a — <reason>` so the downstream execution workflow knows not to wire a flag.
- **Spec is silent:** stop. Bounce back to brainstorming — the spec is non-conformant and the rollout decision is missing. Do not invent one in planning.

**Feature-flag provider (extension point — `feature_flags`):** if a feature-flag provider is configured in your environment, use its naming conventions and dispatch its configuration skill at execution time. If no feature-flag provider configured — see the extend guide in `docs/DEGRADATION.md`. The touchpoint-mapping decision above still happens; only the provider-specific wire-up is skipped.

Test coverage for the flagged behavior must appear in the slice test contracts: any slice that adds a gated code path needs tests for both flag states (on and off). Treat this like the existing TDD rule — it has no carve-out.

### 7b. Map Observability & Failure Visibility Touchpoints (mandatory)

Read the spec's `Observability & Failure Visibility` section.

- **Signals named:** the plan must own them. For each declared health check / metric / soak observable, identify the slice that introduces or modifies it. If the signal is "extend `<ModuleName>`," name the file:line. If the signal is a new metric, name the emission event and the emission site (`file:fn`). If a new check is needed, allocate a slice (or part of one) for the check, its registration on the right surface(s), and the "not configured → degrades visibly" test case.
- **n/a (any variant):** record the spec's reason verbatim in the plan's `Observability & Failure Visibility` block so the downstream execution workflow and reviewers know not to look for it.
- **Spec is silent:** stop and bounce back to brainstorming — the spec is non-conformant and the observability decision is missing. Do not invent one in planning.

**Observability provider (extension point — `observability`):** if an observability provider is configured in your environment (alerting rules, health endpoints, metric stores), use its conventions and dispatch its configuration skill at execution time, and add the alert/rule touchpoint to the slice that introduces the metric. If no observability provider configured — see the extend guide in `docs/DEGRADATION.md`. The Observability & Failure Visibility decision still happens; the provider-specific metric naming, alert-rule generation, and health-check wiring are skipped.

Test coverage: any slice that registers a new check or emits a new metric needs tests for the happy path *and* the not-configured / short-circuit branch (the signal must still degrade visibly when the provider is absent).

### 8. Define Slices

Break the feature into buildable slices. Each slice is a vertical cut of functionality that can be built, tested, and committed independently. Order slices so that:

- Slices with unproven unknowns come first
- Each slice produces something testable
- Later slices build on validated earlier ones

**Every slice follows TDD.** Each slice description must include the test contract — the behaviors to prove with failing tests before writing implementation code. Slices that skip or defer tests are not valid slices.

Slices don't need step-by-step implementation detail. The subagent figures out how to build it. What the plan needs is: what the slice delivers, what files it touches, what unknown (if any) it depends on, and what test behaviors prove the slice works.

### 9. Write the Plan

Scaffold the plan with `lore new plan --title "<topic>"` — it renders the generic plan template into your vault's `plans/` directory with valid frontmatter (today's date, kebab-case slug). Then fill in the body sections. Writing the plan into the vault keeps it linkable from session notes and future planning.

If the `lore` CLI is not on PATH, write the plan to a `plans/` directory in your vault manually, mirroring the template shape below.

**Populate `related-subsystems:` frontmatter** from your vault's subsystem profiles, if present — this drives branch-based subsystem recall and links the plan to the areas it touches. List every subsystem the plan touches, not just the primary one. The CLI infers `project:` from the git remote; if it can't, set it explicitly.

If an upstream spec exists, reference it at the top of the plan (`related-spec:` frontmatter) and bump its frontmatter `status: ready` → `status: planned` after the plan is written. Do **not** create a new design spec — the upstream spec is the canonical "what / why" doc; the plan is the "how".

**Issue tracker (extension point — `issue_tracker`):** after writing the plan and before presenting for approval, if an issue tracker is configured in your environment, advance the corresponding ticket to the appropriate status (e.g. "Requirements Under Development" or equivalent). If no issue tracker configured — status sync skipped. The plan write still happens; only the ticket advancement is skipped.

The plan template (`lore new plan`) renders these canonical sections — fill each in:

- **Goal** — one or two sentences
- **Architecture** — 2-3 sentences about the approach
- **Given Axioms** — the ground truth this plan rests on. Each axiom must be either (a) verifiable at a file:line citation, (b) traced to a recorded decision/ADR, or (c) a constraint stated by the user. If you'd need to investigate to know it's true, it belongs in **Known Unknowns**, not here.
- **Rollout & Gating** — mirror the spec's decision: flag key + default state + touchpoint list, or `n/a — <reason>`.
- **Observability & Failure Visibility** — mirror the spec's decision: the failure signal and its emission site, or `n/a — <reason>`.
- **Known Unknowns** — checkbox list; each notes which slice it blocks.
- **Slices** — each slice carries Delivers / Test contract / Files, plus "Unknown to resolve first" and "Depends on" where applicable.

Leave the `## Council Review` section for Step 9.5 to append — do not pre-fill it.

### 9.5. Council Review (mandatory)

After the plan is written and before presenting it for approval, dispatch a council-lite review. Four council member subagents (`council-builder` / `council-reliability` / `council-security` / `council-advocate`) review the plan + its linked spec in parallel; the main session synthesizes their findings and gates approval on disposition of any Critical findings.

This step is mandatory on every plan. There is no skip flag — calibration is tuned via the per-lens Critical bars below, not via per-invocation opt-outs.

**Dispatch:** make four parallel `Agent` tool calls — one each to `council-builder`, `council-reliability`, `council-security`, `council-advocate` — in a single message so they run concurrently. Use the same prompt template for every member, substituting the lens label and the lens-specific Critical bar block (defined below).

**Substitution rules** (apply BEFORE sending the prompt to each member; do not include these notes in the dispatched text):
- `<plan-path>` — absolute path to the freshly-written plan file
- `<spec-path>` — absolute path to the plan's linked spec. If the plan has no `related-spec` frontmatter, substitute the entire `Spec: <spec-path>` line with `Spec: none — review against the plan's own Goal and Architecture blocks`
- `<lens>` — one of `Builder`, `Reliability`, `Security`, `Advocate` (one per dispatch)
- `<lens-critical-bars>` — the matching block from "Per-lens Critical bars" below

**Prompt template:**

```text
You are being dispatched by the planning skill's mandatory council-lite review step. Review the implementation plan and its linked spec against your lens (<lens>).

Plan: <plan-path>
Spec: <spec-path>

Read both files in full. Apply YOUR lens (<lens>) to identify gaps in the plan that should block approval (Critical), gaps that should be addressed but not block (Important), and observations worth noting (Minor).

Output shape — REPLACE your usual ~400-600 word output with this constrained shape:
- ≤300 words total
- Categorize findings as Critical / Important / Minor
- ≤2 Critical findings (downgrade overflow to Important; forced prioritization is the point)
- Every Critical finding includes: a one-line "what concretely fails" (a specific failure scenario, not "this could be a problem"), and a one-line suggested fix
- No speculative Critical findings — if the finding requires guessing about future state, user behavior, or scale, downgrade to Important
- One-line Confidence at the end

Your lens (<lens>) Critical bar:
<lens-critical-bars>

Cross-cutting Critical you may also raise (any lens):
- Spec drift: plan's slices, summed, don't satisfy spec's acceptance criteria
- Hidden scope expansion: plan touches a subsystem the spec didn't claim
- Reversibility unnamed: plan deploys something hard to roll back without naming rollback path

Required output format:

## Findings
- [Critical] <issue>: <one-line what concretely fails>. Suggested: <one-line fix>.
- [Important] <issue>: <one-line>. Suggested: <one-line>.
- [Minor] <issue>: <one-line>.

## Confidence
<one line — low | medium | high, with brief reason>

Do not include sections you'd normally include in your usual full-length response. For this council-lite review the constrained output above is the whole response.
```

**Per-lens Critical bars** (paste the matching block into each member's dispatch):

*Builder:*
- Slice ordering creates a dependency that can't be tested
- Architecture choice contradicts a declared axiom in the plan
- Producer slice's contract isn't proven by tests but a consumer slice depends on it
- Plan introduces a new abstraction layer for a single caller (premature)

*Reliability:*
- A slice has no test contract, OR test contract is vacuous
- New code path's failure mode is invisible (no health check, metric, log, soak observable) AND the spec's Observability & Failure Visibility block says `n/a — soak-invisible` without substantive reason
- Plan removes existing test coverage without replacement
- A slice does irreversible work without dry-run / preview / staged rollout
- A destructive migration or backfill runs without a gated, replayable console (the ORM / query layer or migration/backfill console) instead of an ad-hoc one-shot

*Security:*
- New authenticated endpoint without named authz check
- New user-supplied input hitting the ORM / query layer without named sanitization
- New log / event / metric containing PII or a user identifier without explicit redaction
- Secret in source / config without using the existing secret-management pattern
- Admin-only behavior exposed to non-admin paths

*Advocate* — dual rule, apply the higher bar for internal admin UX:

End-user-facing (the mobile client, the public web surface):
- Stuck state with no escape
- Primary flow 3+ clicks where 1 is industry-standard
- Developer-jargon error messages
- Missing empty / error / loading states
- A change tested on only one platform but breaks an existing flow on another

Internal admin UI — Critical ONLY when at least one holds:
- (a) No workaround exists
- (b) High-frequency daily workflow with compounding friction (e.g. 1-click → 10-click for a 50×-daily task)
- (c) Feedback ambiguity that propagates bad decisions downstream

Otherwise internal-admin findings are Important at most. Admin users tolerate friction; bikeshedding internal UX is high-cost.

**Synthesis** (main session, NOT a subagent):

After all four members return:
1. **De-duplicate by issue, not by member.** If two members raised the same finding (e.g. Security and Reliability both flag a missing audit log), present it once, grouped by the issue, noting which lenses raised it.
2. **Auto-downgrade speculative Criticals.** If a Critical finding is vague ("this could be a problem"), requires guessing about scale / future state / user behavior, or doesn't name a concrete failure scenario, reclassify it as Important during the consolidated presentation. State explicitly which findings were downgraded and why, both (a) in the user-facing message and (b) in the persisted `## Council Review` section under Important with a `(downgraded from Critical: <reason>)` parenthetical so future calibration audits can detect demote-heavy synthesizers.
3. **Present the consolidated list** to the user, grouped by severity (Critical → Important → Minor). Briefly note total member count behind each finding when more than one member raised it.

**Disposition (required for every Critical):**

For each Critical finding, the user assigns one of:
- `resolved` — user edits the plan inline to address the finding
- `bounced-back-to-spec` — finding changes spec objectives / AC / non-goals; stop planning, route back to brainstorming
- `accepted-as-risk: <reason>` — explicit acceptance, recorded for audit
- `disputed: <reason>` — user disagrees with the finding; recorded for audit

Important and Minor findings do NOT require dispositions — they are logged for the audit trail only.

**Persistence:** append a `## Council Review` section to the plan file capturing all findings and the disposition for each Critical. Structure:

```markdown
## Council Review

*Reviewed at:* YYYY-MM-DDTHH:MM:SSZ
*Members dispatched:* council-builder, council-reliability, council-security, council-advocate

*Critical:*
- <finding> (raised by: <lenses>) — *Disposition:* `<one of: resolved | bounced-back-to-spec | accepted-as-risk: <reason> | disputed: <reason>>`

*Important:*
- <finding> (raised by: <lenses>)

*Minor:*
- <finding> (raised by: <lenses>)
```

Concrete example (populated):

```markdown
## Council Review

*Reviewed at:* 2026-05-22T19:42:11Z
*Members dispatched:* council-builder, council-reliability, council-security, council-advocate

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

**Re-review:** no automatic re-review after the user resolves Critical findings inline. The user attests the fix is in (or that the finding is accepted/disputed), and planning proceeds. This keeps mandatory-review cost bounded; if first-pass calibration is wrong, tune the per-lens bars in this file rather than adding iteration cycles.

**Hard-floor gate:** the "reply `execute` to hand off" prompt in Step 10 must NOT be printed until every Critical finding has a disposition. Important and Minor findings do not block.

### 10. Present for Approval

Share the plan path and a short summary, then wait for explicit user approval before writing any implementation code. Do **not** call `ExitPlanMode` — this skill runs outside plan mode so the plan can be written directly into the vault.

**Before printing the handoff prompt, confirm every Critical finding from Step 9.5 has a disposition recorded in the plan's `## Council Review` section.** If any Critical is undisposed, do not print the handoff prompt — return to disposition gathering with the user.

End the presentation with an explicit handoff prompt so the trigger is unambiguous, e.g.:

> "Plan is written to your vault. Reply **execute** to hand off to `subagent-driven-development` and start building slice by slice, or call out anything that needs adjustment first."

The word `execute` (or similar verbs: `build`, `start`, `go`, `ship it`) is what pulls in the `subagent-driven-development` skill — don't rely on implicit continuation.

## Key Principles

- **Design the whole, build in slices** — understand the full picture, then prove and build incrementally
- **Tests before code, always** — every slice follows TDD. The plan defines *what* to test; a test-driven-development skill enforces *how*. No slice ships without a failing test first.
- **Prove before building** — if a slice depends on an unknown, resolve the unknown first
- **Plans are disposable** — if an unknown is invalidated, the plan changes
- **State unknowns explicitly** — so they can be caught early
- **Flag surprises immediately** — unexpected behavior during implementation may invalidate the direction
