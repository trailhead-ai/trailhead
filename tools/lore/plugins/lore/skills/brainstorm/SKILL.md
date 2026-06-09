---
name: brainstorm
description: >
  Use BEFORE planning, when an idea is still fuzzy and needs discovery. The goal is to flesh out
  boundaries, surface unknowns, poke at edges, and produce a frozen spec (problem, objectives,
  acceptance criteria, non-goals, UI direction) so that planning can be perfunctory and
  execution-focused.
  TRIGGER when: user says "thinking about", "what if", "exploring", "noodling on", "should we",
  "wondering about", "feeling out", "kicking around", "let's iterate on", or invokes /brainstorm
  explicitly.
  DO NOT TRIGGER when: user uses concrete verbs ("implement", "fix", "add", "build") without
  exploration framing, or has already decided what to do.
---

# Brainstorming

Discover the shape of the thing **before** committing to how to build it. Most discovery happens
here so that planning is mechanical and execution is unsurprising.

**A spec is a frozen artifact.** Once finalized, it is not edited. New thinking → new spec, with
a reference back to the prior one.

## Skip Gate

**Do NOT use this skill for:**
- Bug fixes (use a systematic-debugging skill or equivalent)
- Tasks where the user has already decided the *what* and just wants the *how* (jump to planning)
- Single-file changes with obvious intent

If the idea is concrete enough that the next question is "how do we build it," brainstorming is
done — go to planning.

**Inline vs. dispatched:** This skill runs brainstorming inline in the current session — use when
you want interactive back-and-forth with the user. If brainstorming is a step inside an automated
workflow that pauses because a slice surfaced an objectives-level question, dispatch a planner
subagent instead — it covers the full brainstorm → spec → plan arc in an isolated context and
returns a summary.

## Process

### 1. Frame

- Restate the idea in one paragraph using your own words. Confirm with the user.
- Identify touched areas (cross-reference with area profiles in `$LORE_VAULT/areas/`
  if your vault has area profiles).
- Pull related prior art **for reference only**:
  - Existing specs in `$LORE_VAULT/specs/` on overlapping topics
  - Relevant decisions in `$LORE_VAULT/decisions/`
  - Prior dead-ends in `$LORE_VAULT/dead-ends/`
  - Active lessons in `$LORE_VAULT/lessons/` for the touched areas — each carries a
    prevention check that should shape acceptance criteria or non-goals
- **For cross-cutting topics** spanning multiple areas, if a knowledge-synthesis subagent is
  available (such as `lore:lore-librarian`), dispatch it with a synthesis question ("what do we
  know about X, and what's already been decided / tried / deferred?") rather than reading each
  note yourself. If no such subagent is configured, read the relevant notes directly.
- Never modify a prior spec. If this work supersedes one, link it from the new spec's `Related`
  section.

### 2. Poke at Edges

This is the heart of the skill. Sweep across discovery dimensions and surface the questions that
*would shape the design if answered differently*. Batch them, rank by impact, and ask the user to
answer / defer / accept-as-risk.

Cover at minimum:

- **Boundaries:** What's the empty state? Max state? Concurrent state? Partial / interrupted state?
- **Failure modes:** What breaks when an upstream dep is down? Network fails? User does the
  unexpected thing? Race conditions?
- **Hidden assumptions:** What are we assuming about users, data shape, scale, environment,
  permissions, timing?
- **Scope:** Is this the real problem or a symptom? What's adjacent that we're explicitly *not*
  doing?
- **Reversibility:** Can we ship and undo? What's the migration cost if we change our mind?
- **Migration / backfill:** Are there existing users / data / state affected? What happens to them?
- **Failure visibility:** When this breaks in prod, what's the *first* signal a human sees?
  Latency to detection matters as much as the existence of the signal. The deeper "does a signal
  exist at all" question is handled in the mandatory Observability step below.
- **Blast radius:** Who else is affected — other teams, other surfaces, other code paths, other
  clients?

Don't ask all dimensions every time. Pick the ones with real ambiguity for *this* idea, present
them as a ranked batch, and let the user pick what to dig into.

### 3. Map Unknowns and Resolve

For each open question raised in step 2, route it:

- **Resolve now** — work through it together until there's a clear answer.
- **Defer** — explicitly capture as a deferred item via `/lore:defer` (or your vault's defer
  command) with a revisit condition. Note in spec.
- **Accept as risk** — acknowledge in spec under "Open Questions / Risks" with mitigation if any.

Non-obvious choices made during this step → capture via `/lore:decision`.

### 4. Iterate UI / UX (when applicable)

If the idea has a user-facing surface, settle the direction in conversation before locking
objectives.

1. **Identify the surface(s).** Which parts of the product does this touch? Describe them.

2. **Settle the direction in conversation.** Talk through the views/states needed (empty,
   populated, error, edge cases), the primary actions, the information hierarchy.

3. **Design mockup (extension point — `design_mockup`):**
   - If a design-mockup tool is configured in your environment, dispatch it with a structured
     brief describing the surface, states, and interaction flow. Reference the output from the
     spec's UI Direction section.
   - If no design-mockup tool is configured — note the mockup step is skipped (design-mockup tool
     not configured) and describe the UI direction verbally in the spec instead.

4. **Iterate.** For follow-on edits, work in conversation or re-dispatch the mockup tool if
   available.

Don't generate mockups for backend-only or infrastructure changes — describe the surface verbally
instead.

### 5. Decide on Rollout & Gating (mandatory)

Before writing the spec, decide whether this work ships behind a feature flag or gating mechanism.
Every spec must answer this — internal-only refactors and pure infrastructure/docs changes use the
one-line "n/a" escape, but the question is never skipped.

Ask:

- **Does this need a flag?** Default to yes for any user-visible behavior change, schema or
  migration with a meaningful blast radius, third-party integration, or anything you'd want to
  dark-launch / ramp / kill-switch.
- **If yes:** name the flag key, the rollout shape (boolean vs. multivariate), the kill criteria,
  and a one-line cleanup condition.
- **If no:** record a one-line reason (`n/a — internal refactor`, `n/a — docs only`, `n/a — bug
  fix with no behavior change`).

**Feature-flag provider (extension point — `feature_flags`):**
If a feature-flag provider is configured in your environment (such as a flag management tool or
service), use its naming conventions and dispatch its configuration skill if one is available.
If no feature-flag provider configured — see the extend guide in `docs/DEGRADATION.md`. The
Rollout & Gating decision still happens; the provider-specific implementation details are skipped.

Capture the decision in the spec under a `Rollout & Gating` section. The downstream planning
skill reads this section to know whether to design flag touchpoints.

### 5b. Decide on Observability & Failure Visibility (mandatory)

Every spec must declare what signal appears when this work breaks in production. Internal-only
refactors, pure docs, and tooling changes use named `n/a` escapes, but the question is never
skipped.

Answer:

- **What signal appears when this breaks?** Describe the observable symptom (log line, error rate,
  metric spike, health check flip) and where it appears. Latency to detection matters.
- **Is there an existing check or metric that covers this?** If yes, name it. If no: note whether
  you will add one, extend an existing one, or accept `n/a — <reason>`.
- **Soak observable.** When the feature ships and is then broken, what specifically changes in a
  health or monitoring system? If nothing changes, the answer is `n/a — soak-invisible: <reason>`
  — and the reason must name what was considered and why it was rejected. Bare `n/a —
  soak-invisible` is not template-conformant.

**Observability provider (extension point — `observability`):**
If an observability provider is configured in your environment (alerting rules, health endpoints,
metric stores), use its conventions and dispatch its configuration skill if one is available.
If no observability provider configured — see the extend guide in `docs/DEGRADATION.md`. The
Observability & Failure Visibility decision still happens; the provider-specific wiring is skipped.

Capture in the spec under an `Observability & Failure Visibility` section. Downstream planning
reads it to assign signal-emission ownership to slices.

### 6. Write the Spec

Run `lore new spec --title "<topic>" --project "<project>"` to render the template and write the
note to `$LORE_VAULT/specs/`. The template creates a dated file with valid frontmatter that passes
the status validator.

The template (see `lore new spec`) contains these canonical sections — fill each in:

- **Problem** — what situation or gap is being addressed? Why now?
- **Objectives** — measurable outcomes, in user / outcome terms
- **Acceptance Criteria** — bulleted, testable; "done" looks like this
- **Non-Goals** — what you are explicitly NOT doing; as important as objectives
- **Constraints** — technical, business, timing, or organizational limits
- **UI Direction** — verbal description of the user-facing surface; link to any design artifacts;
  `n/a` if no UI surface
- **Rollout & Gating** — mandatory; flag strategy or one-line n/a reason
- **Observability & Failure Visibility** — mandatory; failure signal or one-line n/a reason
- **Open Questions / Risks** — questions deferred or accepted as risk
- **Related** — prior specs, decisions, designs

After `lore new spec` writes the file, open it with an editor and fill in the body sections.

### 7. Exit Gate

Before declaring brainstorming done, verify the checklist:

- [ ] Objectives are clear and outcome-framed
- [ ] Acceptance criteria are testable and bounded
- [ ] Non-goals are explicit
- [ ] Open questions are resolved, deferred, or accepted-as-risk (none unaddressed)
- [ ] UI direction is locked (if applicable) — mockups linked or verbal description written
- [ ] **Rollout & Gating section is filled in** (flag strategy OR `n/a — <reason>`). Never blank.
- [ ] **Observability & Failure Visibility section is filled in** (signal named OR `n/a —
  <reason>`). `n/a — soak-invisible` must name what was considered. Never blank.
- [ ] Spec is written and the path is shared with the user

**Issue tracker (extension point — `issue_tracker`):**
If an issue tracker is configured in your environment, advance the corresponding ticket to the
appropriate status (e.g. "Requirements Under Development" or equivalent) after setting
`status: ready` on the spec. If no issue tracker configured — status sync skipped. The spec
status update still happens.

If all checklist items are green, propose the handoff:

> "Spec is at `$LORE_VAULT/specs/...`. Ready to flip status to `ready` and hand off to planning
> — agree?"

On user agreement, update the spec frontmatter `status: ready` and stop.

**Cross-plugin handoff (forge plugin):**
The `planning` skill lives in the forge plugin — install forge to continue into planning, or write
your implementation slices directly from this spec. Do not enter planning yourself from within
brainstorm — let the user invoke it explicitly so the planning skill loads cleanly.

## Status Lifecycle

The spec frontmatter `status` field tracks where the idea is in its lifecycle:

- `draft` — actively brainstorming; spec is being edited.
- `ready` — brainstorming complete, spec frozen, ready for planning.
- `planned` — a plan exists referencing this spec.
- `complete` — the work has landed.

The lore status guard enforces this vocab. Do **not** use `shipped` or other off-vocab values —
the pre-commit hook will reject the commit.

Once `status: ready`, the spec is **frozen**. No more edits. New thinking on the same topic
creates a new spec with a `Related → Prior specs` link back.

## Bounce-Back from Planning

If planning (or implementation) surfaces something that would change a spec's **objectives,
acceptance criteria, or non-goals**, do not edit the spec — re-enter brainstorming:

1. Stop planning / implementation.
2. Invoke brainstorming to work through the new dimension.
3. Produce a new spec referencing the prior one in `Related`.
4. Resume planning against the new spec.

If the surfaced item is **task-level uncertainty** (how to structure a query, which library to
use, what to name a module), resolve it inline in planning. The bounce-back rule is for *what /
why* shifts, not *how* shifts.

## Key Principles

- **Discovery is the work.** The output of brainstorming isn't a spec — it's a shared
  understanding. The spec is the artifact of that understanding.
- **Poke at edges relentlessly.** Most planning surprises are brainstorming failures. Better to
  surface an ugly question now than rediscover it mid-implementation.
- **Specs are frozen.** They capture a moment of alignment. Don't retrofit; supersede.
- **Defer explicitly.** Unanswered questions become silent assumptions. Either resolve, defer with
  a revisit condition, or accept as a named risk.
- **UI before objectives lock.** Visual iteration changes what's possible and surfaces
  requirements that pure prose hides.
- **Hand off cleanly.** Brainstorming ends when the spec is frozen and the user agrees. Planning
  starts fresh against a stable spec.
