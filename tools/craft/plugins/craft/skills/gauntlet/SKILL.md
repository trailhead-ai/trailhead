---
name: gauntlet
description: >
  Run the adversarial review — the gauntlet — on a draft spec or a draft adr before it freezes. For
  a spec, eight parallel passes attack it from independent angles: fact verification, premise attack,
  the four council lenses, an internal-consistency audit, and a plan-divergence probe. For an adr, the
  same roster runs minus the divergence probe (no analogue for a decision document) — seven passes.
  The main session adjudicates, the user dispositions every Critical, and the record is stamped with
  review provenance before it flips (a spec to `ready`, an adr to `active`).
  TRIGGER when: brainstorming has produced a spec and is at its exit gate (the gauntlet is a
  mandatory step there), a draft adr needs review before it flips to `active`, or the user says "run
  the gauntlet", "gauntlet this spec", "gauntlet this adr", "adversarial spec review", "review the
  spec/adr before it freezes", or invokes /craft:gauntlet explicitly.
  DO NOT TRIGGER when: reviewing an implementation plan (planning's Council Review step covers that),
  reviewing written code (use review), the spec is already `ready`, or the adr is already `active` —
  a frozen record is not re-gauntleted; new thinking creates a new spec, and a change in decision
  creates a new, superseding adr. A distilled (backward) adr also never triggers this skill — the
  distill disposition owns its flip.
---

# The spec gauntlet

A spec is the most expensive artifact in the pipeline to get wrong. Every plan, every slice, and
every line of code downstream inherits its mistakes — and by the time the mistake is visible, it is
load-bearing. The gauntlet is the last point where the spec is still cheap to change.

Eight passes attack the spec in parallel, each from an angle the others structurally cannot see. The
main session adjudicates what comes back. Nothing freezes until the user has dispositioned every
Critical.

The same review runs against a draft **adr** with an adapted, seven-pass roster and a different
freeze target — see "Reviewing an adr" below. Everything else in this document is written for the
spec case; the adr section states only its deltas.

## Two independent failure axes

This is the calibration that justifies the cost, and it is not obvious:

**A spec can be wrong about the world, or underdetermined about the design — and these are
independent.** A spec can have every factual claim confirmed and still be a bad spec, because
"correct about what exists" and "sufficient to build from" are different properties. In the pilot
that established this protocol, **every one of the spec's factual claims verified clean — and the
other passes still produced seven design-changing findings.** A clean fact pass is not evidence the
spec is sound; it is evidence of exactly one thing.

Both axes need passes pointed at them. That's why the roster is what it is.

## Mandatory

This step runs on **every** spec before it flips to `ready`. There is no skip flag. Calibration is
tuned through the per-lens Critical bars in `_shared/council.md` and the severity rules below —
never through per-invocation opt-outs. A spec that "obviously doesn't need it" is the spec the
premise pass most often reframes.

## Process

### 1. Resolve and read the spec

Take the spec record id from the invocation, or — when brainstorming hands off at its exit gate —
the spec it just wrote. Read it **in full** (`lore record show <spec-id>`). Confirm its status is
`draft`; a `ready` spec is frozen and is not re-gauntleted (see brainstorm's Status Lifecycle — new
thinking creates a new spec instead).

**Resolve its absolute path too** (`lore record show <spec-id> --json` carries it; `lore search`
prints it). The passes run in isolated contexts and most of them have no `Bash` — they open the spec
with `Read`, so an absolute `<spec-path>` is what you hand them, exactly as planning's Council Review
hands the council its `Spec:` pointer. Never ship a bare record id to a pass that cannot resolve it.

### 2. Decompose the claims (main session, mechanical)

Walk the spec statement by statement and type each one. This is bookkeeping, not judgment — do it
inline, don't dispatch it:

| Type | What it is | Who attacks it |
|---|---|---|
| **Fact** | A falsifiable assertion about the world as it is today | fact-verification pass |
| **Premise** | A load-bearing assumption the spec needs in order to make sense | `premise-attacker` |
| **Decision** | A choice made, with live alternatives | council lenses, `divergence-prober` |
| **Requirement** | Something the built thing must do | `consistency-auditor` |

The output is a numbered claim list. It is what the fact pass verifies against and what the
adjudicator maps findings back onto. **Unstated premises count** — a spec's most dangerous
assumptions are the ones it never wrote down, so add them to the list as you spot them.

### 3. Dispatch the eight passes (parallel, isolated)

Make **all eight `Agent` calls in a single message** so they run concurrently. Each pass runs in an
isolated context and sees only what its prompt points it at.

| # | Pass | Agent | Give it |
|---|---|---|---|
| 1 | Fact verification | `Explore` | The spec path + the **Fact** claims from step 2 |
| 2 | Premise attack | `premise-attacker` | The spec path + the **Premise** claims from step 2 |
| 3–6 | The four lenses | `builder`, `breaker`, `attacker`, `advocate` | Per `_shared/council.md` |
| 7 | Consistency audit | `consistency-auditor` | The spec path |
| 8 | Divergence probe | `divergence-prober` | The spec path |

**All eight passes are required.** If any pass agent is not installed (craft's subagents are
selectable by name, so a hand-picked install can omit one), **say which one and stop** — do not
quietly run seven and present the result as a gauntlet. A review that silently lost its premise
pass is worse than no review, because it still ends in a `ready` spec. Name the missing agent, tell
the user to install it, and leave the spec at `draft`.

**Pass 1 — fact verification.** Dispatch `Explore` with the Fact claims and this instruction:

```text
Verify each claim below against the codebase AND against sibling spec records in the vault.
Evidence only — no opinions, no suggestions, no design commentary.

For each claim return exactly one of:
  CONFIRMED — with a file:line or record name proving it
  REFUTED   — with a file:line or record name disproving it
  UNVERIFIABLE — with one line on what you'd need to settle it

Claims:
<the numbered Fact claims from step 2>
```

Sibling specs are **not optional context here** — a spec's factual claims include what it assumes
about capabilities *other specs depend on*, and that dependency lives in the sibling's text, not in
the code. In the pilot, the highest-severity finding of the entire run came from a sibling spec's
dependency on a capability the spec under review proposed to delete. Code-only verification would
have returned a clean sweep and missed it.

**Pass 2 — premise attack.** Dispatch `premise-attacker` with the spec path and the Premise claims.
It is the only pass licensed to reject the spec's framing outright — do not soften its mandate in
the prompt.

**Passes 3–6 — the four lenses.** Dispatch per the dispatch contract in `_shared/council.md` —
read it; do not re-inline the roster, prompt template, or bars here. Fill the substitution tokens
**before** sending each member its prompt (never ship a literal `<token>`):

- the context-pointer line →
  ```text
  Review the draft spec against your lens.
  Spec: <spec-path>
  ```
- `<lens-critical-bars>` → the matching block from **"Per-lens Critical bars — spec review"** in
  `_shared/council.md`. **Not** the plan bars — a spec has no slices, and the plan bars fire on
  things that don't exist yet.
- `<cross-cutting>` → the empty string (the plan-drift block is planning-only).

**Passes 7–8 — consistency audit and divergence probe.** Dispatch `consistency-auditor` and
`divergence-prober` with the spec path. Their prompts are self-contained; they need no extra framing.

### 4. Adjudicate (main session, NOT a subagent)

Eight passes return on the order of thirty raw findings. **Handing that list to the user is not
adjudication — it is delegation of the work you were dispatched to do.** Consolidate first:

1. **De-duplicate by issue, not by pass.** Two passes reaching the same finding from different
   angles is one finding, annotated with both.
2. **Weight cross-pass convergence.** When independent passes — which could not see each other's
   work — converge on the same issue, that is the **strongest severity signal available to you**.
   Rank convergent findings above single-pass findings of nominally equal severity.
3. **Drop the editorial.** Wording preferences, section-ordering suggestions, and style notes are not
   spec defects. Cut them silently.
4. **Auto-downgrade speculative Criticals**, per the synthesis rules in `_shared/council.md`. State
   which and why.
5. **Spot-verify contentious claims.** Any finding that would be expensive to act on, that
   contradicts another pass, or that arrives in a transcript reading anomalously (over-confident,
   thin on evidence, or wandering outside its stated lane) gets checked yourself before it reaches
   the user. Do not launder an unverified subagent claim into a recommendation.
6. **Reorganize by spec section, not by pass.** The user's next action is *editing the spec*, so the
   deliverable is a change list keyed to the sections they'll edit — Problem, Objectives, Acceptance
   Criteria, Non-Goals, Constraints, UI Direction, Open Questions. A per-pass finding dump forces the
   user to do that mapping themselves.

Present the consolidated change list grouped **Critical → Important → Minor**, with the passes
behind each finding named alongside it. Write every finding in the shape "How a finding reads"
defines in `_shared/council.md` — that shape is what makes the list readable by someone who has
not read the spec.

### 5. Disposition (required for every Critical)

For each Critical, the user assigns exactly one:

- `resolved` — the spec is edited to address it. It is still `draft`; edits are free here, which is
  the entire point of reviewing now.
- `reframed` — the finding invalidates the spec's framing. **This spec does not freeze.** Return to
  brainstorming, write a new spec, mark this one `superseded` with a `Related → Prior specs` link.
  This is the premise pass's characteristic outcome, and it is a *success* of the gauntlet, not a
  failure of the spec — a reframe here costs a conversation; the same reframe discovered mid-build
  costs the build.
- `accepted-as-risk: <reason>` — explicit acceptance, recorded for audit.
- `disputed: <reason>` — the user disagrees with the finding; recorded for audit.

Important and Minor findings need no disposition — they are logged for the audit trail.

### 6. Stamp and freeze

Once every Critical is dispositioned and any `resolved` edits are folded in, append the review
provenance to the spec's `Related` section (or a `## Gauntlet` section), then flip it:

```markdown
- Adversarial spec review (gauntlet, <date>): 8 passes — facts <n>/<n> confirmed; <n> design-changing
  findings folded in (<one-clause each>). Criticals dispositioned: <n> resolved, <n> accepted-as-risk,
  <n> disputed.
```

Then `lore record update <spec-id> --status ready` and hand off to planning. Do not enter planning
from inside the gauntlet — let the user invoke `/craft:plan` so it loads cleanly. End the wrap-up
with the handoff command **fully formed** — the real spec-id, never a `<placeholder>` (e.g.
`/craft:plan spec/streaming-export`) — so the user can paste it into a fresh session as-is.

If any Critical was dispositioned `reframed`, the spec instead goes `superseded` and the handoff is
back to brainstorming, not forward to planning — end with `/craft:brainstorm` instead.

## Reviewing an adr

The gauntlet also runs against a draft `adr` record before it flips to `active` — same mandate,
same "no skip flag," an adapted roster, and a different freeze target. Steps 1, 2, and 4 above carry
over unchanged (resolve the record and its absolute path, decompose its claims, adjudicate in the
main session); this section states only where an adr target changes the rest.

**Resolving the record:** `lore record show <adr-id>` in place of `<spec-id>`. Confirm its status is
`draft`; an `active` adr is frozen by convention (`templates/adr.md`) and is not re-gauntleted — a
change in direction is a new, superseding ADR, not an edit to this one.

### The adapted roster — 7 passes, no divergence probe

| # | Pass | Agent | Give it |
|---|---|---|---|
| 1 | Fact verification | `Explore` | The adr path + the Fact claims |
| 2 | Premise attack | `premise-attacker` | The adr path + the Premise claims |
| 3–6 | The four lenses | `builder`, `breaker`, `attacker`, `advocate` | Per `_shared/council.md`, using **Per-lens Critical bars — adr review** for `<lens-critical-bars>` |
| 7 | Consistency audit | `consistency-auditor` | The adr path |

**The divergence probe is dropped.** Its method is constructing two materially different
*implementations* that both satisfy the spec under review — a decision document has nothing to
implement two versions of, so the pass has no analogue here. This is a roster change, not a corner
cut: everything else that can attack an ADR still does.

**All seven passes are required.** Same rule as the spec roster: if any pass agent is not installed,
name it and stop — do not quietly run six and present the result as a gauntlet. Leave the adr at
`draft`.

### The gauntlet owns the flip, directly to `active`

The adr vocab (`draft`, `active`, `superseded`, `dropped`) has no `ready` — there is no intermediate
frozen-but-inactive state the way a spec has. Once every Critical is dispositioned, the gauntlet
flips the record directly:

```
lore record update <adr-id> --status active
```

(A Critical dispositioned `reframed` routes the adr to `dropped`, not `superseded` — it never went
`active`, so there is no predecessor decision for it to supersede.)

### Supersession writes both directions, on the forward path too

Distill is not the only writer of an ADR — the forward path (brainstorm's altitude gate → this
gauntlet) authors and activates ADRs as well, and supersession's "both directions" contract binds
here identically: an `active` ADR the gauntlet is about to supersede must end this flip with its
predecessor flipped `superseded` and back-linked, not just the new one flipped `active`.

The successor's `--related adr=<predecessor>` edge is set **before** this step — brainstorm (or
whoever authored the draft) writes it at creation, same as any other provenance edge. At
activation, when that edge names an existing `active` ADR, the gauntlet's flip is two writes, in
this order (mirroring distill's pinned internal order):

```
lore record update <adr-id> --status active
lore record update <predecessor-adr-id> --status superseded --related adr=<adr-id>
```

Skipping the second write leaves the predecessor `active` next to its own successor — the same
inconsistent state distill's resume rule exists to heal, except nothing here would ever heal it,
because only distilled ADRs are on distill's resume path.

### Provenance goes to annotations, never the body

The four-section body contract (`templates/adr.md`) is exhaustive — Context, Decision, Consequences,
Alternatives rejected, nothing else.

Gauntlet provenance for an adr target goes to the record's annotations, never the body:

```
lore record update <adr-id> --annotation gauntlet=<date>:7-passes:<n>-resolved,<n>-accepted-as-risk,<n>-disputed --status active
```

### Distilled ADRs skip the gauntlet

Distilled (backward) ADRs skip the gauntlet — the distill disposition owns their flip. An ADR that
`/craft:distill` writes from an already-completed spec is authored and flipped by that ritual
directly; it never routes through this skill.

## Calibration

Held from the pilot runs that established this protocol — these are the failure modes the skill is
shaped to avoid:

- **The premise attack and the divergence probe are the highest-yield passes.** They are also the two
  a naive "just run the council on the spec" version omits. If the budget ever has to shrink, these
  are the last two to go, not the first.
- **A clean fact pass proves one thing only.** See the two-axes section above. Do not let it soften
  the adjudication of the other seven.
- **Convergence beats confidence.** A finding two blind passes reached independently outranks a
  finding one pass asserted forcefully.
- **The adjudicator is a reviewer, not a router.** Consolidation, spot-verification, and section
  mapping are the job. Thirty findings forwarded verbatim is a failed adjudication.
