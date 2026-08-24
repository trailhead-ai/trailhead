---
name: gauntlet
description: >
  Run the adversarial review — the gauntlet — on a draft spec or a draft adr before it freezes. For
  a spec, eight parallel passes attack it from independent angles: fact verification, premise attack,
  the four council lenses, an internal-consistency audit, and a plan-divergence probe. For an adr, the
  same roster runs minus the divergence probe (no analogue for a decision document) — seven passes.
  The main session adjudicates and hands back one compact recommendation — a synthesis, a route,
  and a proposed disposition per Critical — which the user accepts or overrides in a single
  round-trip, before the record is stamped with review provenance and flips (a spec to `ready`,
  an adr to `active`).
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
main session adjudicates what comes back and turns it into one recommendation. Nothing freezes
until the user has accepted that recommendation.

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
6. **Number the surviving Criticals `C1`…`Cn`.** Assign the ids here, at consolidation, in the order
   you will present them. An id is **stable for the rest of the run** — the operator names it to
   override, and the audit trail records it — so never renumber after presenting, not even when an
   override collapses a row's relevance.

Consolidation is not the deliverable. What the operator sees is the recommendation step 5 builds
out of it.

### 5. Recommend, then accept

The operator's job here is to **decide, not to re-derive**. A finding dump makes them re-open the
record to judge each item; a recommendation lets them judge in place. So the default output is one
compact deliverable — target one terminal screen (~40 lines) for a typical run — and one word
("go") is a complete answer to it.

The deliverable is these four parts, in this order — all four whenever there is a Critical to
disposition, and the same parts minus the table on a run that produced none ("Zero Criticals is
still a decision", below):

1. **The narrative synthesis** — prose, and the part that carries the deliverable. Three movements,
   one short paragraph each — what the passes found, whether it holds and where it came from, and
   what to do about it — written in the shape **"How the synthesis reads"** defines in
   `_shared/council.md`. Read it there; do not restate it here. Two things about it are worth
   naming for this caller in particular:

   - **No sentence cap.** Three movements do not fit a fixed sentence count, and a cap tight enough
     to squeeze them squeezes two of the three down into the table instead — which is the reader
     reconstructing the explanation from rows again. What holds the length is the one-screen budget
     above, plus the shared rule that a synthesis which will not fit means the finding set is
     **under-consolidated**: go back to step 4 rather than spend more of the screen.
   - **The interpretive per-pass read is the point.** Eight passes attacked this record from lanes
     that could not see each other, and which of them were right is a judgment only the adjudicator
     is positioned to make. Making it is movement two's whole job; the shared contract names what
     stays forbidden.
2. **The recommended route**, on its own line, by name (below).
3. **The per-Critical table** — supporting detail, not the explanation. The synthesis has already
   said what the findings mean and what you propose doing; the table is the row-level view for
   checking that against the findings, and the handle the operator names to override. One row per
   Critical, in `C1`…`Cn` order:

   | id | finding | proposed disposition | proposed edit |
   |---|---|---|---|
   | C1 | *headline, one line* | `resolved` | *one clause: what the edit does* |

4. **Important and Minor, compressed** to a count plus a one-line theme apiece. They take no
   disposition — they are logged for the audit trail.

**Draft every `resolved` edit in full before you present.** The table's edit clause summarizes text
you have already written, and acceptance applies that text verbatim. An accepted recommendation
must never send you back to compose the edit you promised, because what would land then is text the
operator never approved.

**Full finding detail is not printed by default.** It is one request away ("show me the detail on
C2") and it is retained in the audit trail either way. When you do print it, write the finding in
the shape "How a finding reads" defines in `_shared/council.md` — that shape is what makes a finding
readable by someone who has not read the document under review.

**Security Criticals are never compressed.** A Critical raised by the attacker lens renders its row
in full — the whole finding and the actual proposed edit text, never a clause standing in for it.
Compression is a convenience for the operator, and a one-clause summary of a security finding reads
as reasonable no matter what it elides. This mirrors drift-gate's rule that security surface is
conformance, not quality.

#### Who may propose what

The agent proposes **only `resolved` or `reframed`**. Both are judgments about the document, and
the adjudicator has read every pass that attacked it.

`accepted-as-risk: <reason>` and `disputed: <reason>` are **operator-only overrides**. Both are
judgments about what this project is willing to live with, and their reason text is the operator's
own — quote it, **never drafted for them**. Do not propose either disposition, and do not offer a
reason the operator did not say.

- `resolved` — the record is edited to address the finding. It is still `draft`; edits are free
  here, which is the entire point of reviewing now.
- `reframed` — the finding invalidates the record's framing. Return to brainstorming and write a
  new record. This is the premise pass's characteristic outcome, and it is a *success* of the
  gauntlet, not a failure of the record — a reframe here costs a conversation; the same reframe
  discovered mid-build costs the build.
- `accepted-as-risk: <reason>` — explicit acceptance, recorded for audit.
- `disputed: <reason>` — the operator disagrees with the finding; recorded for audit.

#### The two routes, and the rule that picks one

There are exactly **two** routes. These are their names, here and in both per-mode tails:

| Route | Spec target | Adr target | Handoff |
|---|---|---|---|
| **freeze route** | `ready` | `active` | forward — planning for a spec |
| **reframe route** | `superseded` | `dropped` | back to brainstorming |

The rule that picks one is **total over the disposition vocabulary** — every combination of
dispositions lands on exactly one route, so there is never an outcome left to freelance:

- **Any Critical dispositioned `reframed`**, whether you proposed it or the operator overrode into
  it → the **reframe route**.
- **Every other combination** of `resolved` / `accepted-as-risk` / `disputed`, including a run with
  no Criticals at all → the **freeze route**.

Derive the route; do not choose it. And do not invent a third — a route with no target status is a
record left in a state the lifecycle vocabulary has no name for.

#### Zero Criticals is still a decision

A run that produced no Criticals presents the deliverable anyway — synthesis, route, and the
compressed Important and Minor summary, labeled as a clean run, with no per-Critical table, since
there are no rows for it to hold — and **still gates on operator acceptance**. Clean of Criticals is
not clean of findings: the Important and Minor themes are part of what the operator accepts here,
and a run that presents none of them reads as a sweep that found nothing. A gauntlet never freezes a
record on its own reading of a clean sweep; the clean sweep is the finding, and the operator is the
one who accepts it.

#### Accepting, and overriding in one round-trip

Present, then wait. The operator either accepts ("go") or overrides ("dispute C3, otherwise go").
Overrides apply in **one round-trip**: take every override from that one reply, apply them
together, and do not walk back through the table finding by finding.

- **Echo the full post-override table.** After applying any override, re-render the complete
  `C1`…`Cn` disposition table — **not just the route line** — as the last thing before the accepted
  tail executes. A misapplied override ("dispute C3" recorded against C4) changes nothing the route
  line displays, and the audit trail it lands in is permanent.
- **An override naming an id outside the presented range is rejected.** "dispute C7" against a
  five-row table is an error, not a puzzle: say which ids exist and ask again. **Never map an
  unknown id onto the id you think was meant.**
- **An override with no reason is incomplete.** `accepted-as-risk` and `disputed` carry the
  operator's reason text, and you may not write it for them — so "dispute C3", with nothing said
  about why, is not yet a disposition: ask for the reason and record nothing until they give it.
  **Never record either disposition with a reason you drafted, and never with the reason slot
  empty.** This is the one path by which text you wrote could enter the permanent trail wearing the
  operator's signature.
- **An override off `resolved` withdraws that row's drafted edit.** Every `resolved` row's edit text
  was drafted before you presented, and only the rows still `resolved` once the overrides are
  applied belong to the accepted set. An override to `disputed`, `accepted-as-risk`, or `reframed`
  therefore **removes that row's edit from `$EDITS`**, and the echoed post-override table is what
  says which edits remain. A diff assembled before the override lands the one change the operator
  explicitly declined, permanently, in a record about to freeze.
- **A route-changing override re-presents once.** If applying the overrides changes the route — an
  override that removes the last `reframed`, or one that introduces one — present the revised
  recommendation once more and take acceptance again **before anything is written**. If that reply
  changes the route again, it is a further override round-trip and it re-presents again.
  **The cap is one re-present per route change, not one per run.** What the cap forbids is
  re-presenting a recommendation nothing changed; what it never licenses is writing a route the
  operator has not seen and accepted.
- **An override *into* `resolved` re-presents too, whatever the route does.** Only the rows you
  proposed `resolved` have their edit text drafted, so an override moving a `reframed` row to
  `resolved` produces an accepted row with no edit behind it. Draft that edit, then present once
  more and take acceptance again — including on a run whose route never moved because another
  `reframed` row still holds it. The cap above forbids re-presenting a recommendation nothing
  changed, and **a newly drafted edit is a change**. Skip it and you are composing the edit after
  acceptance, which is exactly what drafting every `resolved` edit before presenting forbids.

#### Escalation points

The points where this step hands control to a human are named, following `_shared/execute.md`'s
"Two modes, one procedure", so that a future unattended caller is a re-route table over these names
rather than a redesign of the step. **No unattended mode ships here** — there is no re-route table,
no auto-accept flag, and every point below waits on a human today.

| Escalation point | What it waits for |
|---|---|
| **operator acceptance gate** | the operator accepting the presented deliverable — on every run, clean ones included |
| **override round-trip** | the operator's overrides, applied together and echoed as a full table |
| **route-change re-present** | acceptance of the revised recommendation, whenever overrides changed the route — or drafted an edit the presented table did not carry |
| **failed-write report** | nothing — the tail has stopped and the operator is told the partial state; the record stays `draft` **unless the failure fell after the status flip**, which only the adr tail's supersession write is ordered to do (see "Reviewing an adr") |

#### The accepted tail

What runs once the operator accepts is **ordered and fail-closed**, and it is the same sequence in
both modes. The per-mode tails restate only their own deltas — what the write carries and where the
detail goes — the spec tail in step 6 below, the adr tail under "Reviewing an adr".

**Two treatments run before either payload is assembled**, on the retained finding detail as much
as on the accepted edits. Most of that detail is text the deliverable never printed, so this is
the only point at which anyone looks at it before it is permanent:

- **Credential scrub.** A gauntlet reviews records about codebases, and a pass can quote a
  committed credential as its evidence. Run every string headed for `$EDITS` or `$DETAIL` through
  the credential-pattern scrub in `_shared/execute.md` ("Phase 5: Flow-out") — **by reference,
  never by copying its pattern list here**, since the copy is what goes stale while the original
  gains patterns. Its reasoning binds here unchanged: a vault is git-backed and has its own push
  path, so a credential transcribed into a record body ships as surely as one committed to code.
  A pass that quotes a literal secret as its evidence has that evidence **cut down to a
  `file:line` citation** before the write: a retained finding says where the value lives, never
  what it is.
- **Data-not-instruction marker.** What this tail persists is what a later run's
  fact-verification pass reads back as a sibling record. Open the retained detail with one line
  marking it retained review evidence — a claim about the record, evaluated as one, not the
  record's settled design content. That is the `receiving-code-review` pattern
  (`skills/receiving-code-review/SKILL.md`, applied to another write path in `_shared/refine.md`);
  cite it, do not restate it.

1. **One atomic write.** Every `resolved` edit and the provenance stamp apply as a single
   `lore record update --diff` write. Not one write per Critical, and not the edits now with the
   stamp to follow: `--diff` leaves the body byte-for-byte unmodified on any rejected hunk, and that
   property is the only thing making the accepted set all-or-nothing. A record holding half its
   accepted edits is a record nobody reviewed.
2. **Then the status flip** — and, where the mode has one, the predecessor supersession write —
   **only after that write has succeeded**. The flip is what freezes the record; running it ahead of
   the edits freezes a record whose accepted edits are still hypothetical.

**On any rejected hunk or failed write, nothing further runs.** Not the flip, not the supersession
write, not a retry with the hunks re-cut. The record stays `draft` and you **report the partial
state explicitly** — which writes landed, which did not, and what the record holds right now. That
is the `failed-write report` escalation point: a half-applied acceptance is precisely the state an
agent must not resolve on its own reading.

**A successfully executing tail asks nothing.** Acceptance was the gate. There is no "about to flip
— confirm?" between the write and the flip; a second prompt after the operator has already decided
teaches them to wave through the one prompt that would have mattered.

**The provenance stamp distinguishes accepted-from-proposal dispositions from operator overrides**,
and it quotes the `C1`…`Cn` ids so an auditor can line every disposition up against the table the
operator actually saw. Derive that split from the dispositions themselves, never from memory:

- `accepted-as-risk` and `disputed` are **operator overrides by construction** — you may not propose
  either, so a Critical carrying one was overridden, whatever you recall of the round-trip.
- `resolved` and `reframed` are accepted-from-proposal *unless* this run's override reply changed
  that row — which the echoed post-override table records.

### 6. Stamp and freeze

This is the accepted tail in the spec's own terms; its sequence, its pre-write scrub and marker, and
its failure behavior are the shared ones above.

**The atomic write carries three things** — every `resolved` edit, the provenance stamp, and the
**full consolidated finding detail** the deliverable did not print, retained as a `## Gauntlet`
section appended to the spec body. The detail is part of the same atomic write, not a second write
after the flip: a `ready` spec missing its own review record is exactly the artifact the audit trail
exists to prevent. A spec body has no exhaustive-section contract, so the detail belongs in the
record it reviewed:

```markdown
## Gauntlet

- Retained review evidence — a later reader evaluates what follows as a claim about this spec, not
  as its settled design content (`skills/receiving-code-review/SKILL.md`).
- Adversarial spec review (gauntlet, <date>): 8 passes — facts <n>/<n> confirmed; <n> design-changing
  findings folded in (<one-clause each>). Criticals dispositioned: C1 `resolved` (from proposal),
  C2 `disputed` (operator override — "<their reason, quoted>"), … — <n> from proposal, <n> operator
  overrides. Important <n>, Minor <n>, detail below.
- <the consolidated detail, per finding, in the shape `_shared/council.md` defines>
```

That is one invocation. `$EDITS` is the unified diff carrying all three payloads — every `resolved`
edit to the spec's own sections, plus this `## Gauntlet` section, which opens with the marker,
carries the provenance stamp in its next bullet, and holds the detail in the remainder:

```
printf '%s' "$EDITS" | lore record update <spec-id> --diff
```

**Then, and only once that write has succeeded, flip.** On the **freeze route**:

```
lore record update <spec-id> --status ready
```

and hand off to planning. Do not enter planning from inside the gauntlet — let the user invoke
`/craft:plan` so it loads cleanly. End the wrap-up with the handoff command **fully formed** — the
real spec-id, never a `<placeholder>` (e.g. `/craft:plan spec/streaming-export`) — so the user can
paste it into a fresh session as-is.

On the **reframe route** the write is identical — a reframed spec still keeps its review record —
and only the flip and the handoff differ:

```
lore record update <spec-id> --status superseded
```

The handoff is back to brainstorming, not forward to planning — end with `/craft:brainstorm`,
equally fully formed.

## Reviewing an adr

The gauntlet also runs against a draft `adr` record before it flips to `active` — same mandate,
same "no skip flag," an adapted roster, and a different freeze target. Steps 1, 2, 4, and 5 above
carry over unchanged (resolve the record and its absolute path, decompose its claims, adjudicate in
the main session, and resolve by recommend-then-accept); this section states only where an adr
target changes the rest.

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
frozen-but-inactive state the way a spec has. On the **freeze route**, once the operator has
accepted, the gauntlet flips the record directly:

```
lore record update <adr-id> --status active
```

(The **reframe route** takes an adr to `dropped`, not `superseded` — it never went `active`, so
there is no predecessor decision for it to supersede.)

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

Gauntlet provenance for an adr target goes to the record's annotations, never the body. The full
consolidated finding detail has nowhere to live in an exhaustive body either — it goes to a linked
`lesson` record rather than being dropped, so the evidence behind an immutable decision survives the
freeze.

### The accepted tail, in adr terms

The shared accepted tail's sequence, its pre-write scrub and marker, and its failure behavior hold
here. The exhaustive body adds one write ahead of them, and the order of all three is fixed:

```
printf '%s' "$DETAIL" | lore record create --kind lesson --title "Gauntlet detail — <adr title>" --related adr=<adr-id>
printf '%s' "$EDITS" | lore record update <adr-id> --diff --annotation gauntlet=<date>:7-passes:<n>-resolved,<n>-reframed,<n>-accepted-as-risk,<n>-disputed:<n>-from-proposal,<n>-operator-override
lore record update <adr-id> --status active
```

1. **The `lesson` record is written first**, carrying the consolidated detail and its
   `--related adr=<adr-id>` edge on the same command — an edge added by a later write is an edge
   that a failure between the two never writes. It is also where the shared stamp's per-Critical
   half lands **in full**: the `C1`…`Cn` dispositions, each one **marked from-proposal or
   operator-override**, and every `accepted-as-risk` / `disputed` reason **quoted in the operator's
   own words**. An annotation is a key/value — it holds the counts and nothing else — so the ids,
   the markers, and the reasons need a record with a body, and this is it. Write the counts alone
   and the operator's stated reason for living with a risk is missing from the trail of a decision
   nothing can edit afterwards. Its body opens with the shared marker line naming it retained
   review evidence: a `lesson` is precisely the sibling record a later pass reads back as prior art.
2. **Then the shared tail's one atomic write** — `$EDITS` is the unified diff of every `resolved`
   edit, and the counts annotation rides the same invocation. `--diff` and `--annotation` apply
   inside a single read-modify-write, so a rejected hunk leaves the body *and* the annotation
   untouched, and the all-or-nothing property the shared tail depends on holds here unchanged. What
   the exhaustive body keeps out is the provenance and the finding detail — never the accepted
   edits, which are edits to Context, Decision, Consequences, and Alternatives rejected and must
   land before anything freezes. An adr flipped `active` without them is an immutable decision no
   acceptance ever reached. The counts alongside the accepted-from-proposal / operator-override
   split are how the provenance stamp renders for an adr, and `<n>-reframed` is what tells an
   auditor of a `dropped` adr which disposition sent it there. **This write runs on every accepted
   run, including one with zero `resolved` Criticals** — `$EDITS` is then an empty diff, which the
   CLI applies as a no-op, and the annotation rides the same invocation regardless. Skipped there,
   the adr flips `active` carrying no provenance at all, because the annotation is the only
   provenance its exhaustive body will ever hold.
3. **Then the status flip** — `--status active` on the freeze route, and, when the successor's edge
   names an `active` predecessor, the two-write supersession above in its pinned order.

The **reframe route** runs the first two writes identically — a dropped adr keeps its review
evidence, which is often the whole reason it was dropped — and differs only in the last one:

```
lore record update <adr-id> --status dropped
```

with no supersession write, since a dropped adr never became the decision of record.

Lesson-first is chosen for the state a crash leaves behind. Its worst surviving artifact is a
`draft` adr with an extra record pointing at it, which is harmless and re-runnable; the reverse
order's is an `active`, immutable decision whose review evidence was never written at all.

**Any failure before the flip stops the sequence**: the adr stays `draft`, and a `lesson` record
already written when a later write fails is **never silently abandoned** — report the orphaned
`lesson` record to the operator by name, so they can re-run or delete it. Give them the lookup as
well:

```
lore search 'kind:lesson related-adr:"<adr-id>"'
```

**The query and the edge are the same `<adr-id>`, spelled the same way.** `--related adr=<adr-id>`
stores that value verbatim, so a query naming the bare adr name matches nothing — and zero results
read as "no orphan" to precisely the operator this report exists to warn. The id carries a `/`, so
it is quoted; unquoted, the query is a parse error rather than a lookup.

That is the `lesson` record's own **forward** edge, which `lore record create` projects as it
writes, so it resolves immediately and needs no `lore reindex` first. (The reverse direction — a
query from the adr's side — is a reindex-only property, and is not what this lookup uses.) Still
report the record name alongside the query, never the query alone: the name is what the operator
acts on, and it survives an index this report cannot inspect.

**A failure after the flip reports differently.** The predecessor's `superseded` back-edge is the
only write ordered after the status flip, so the rule above — the adr stays `draft` — cannot
describe it: the adr is already `active` and immutable, and re-running the flip changes nothing.
Name both records, say that the successor is `active` and the predecessor is still `active` and
unlinked, and hand back the one write that closes it:

```
lore record update <predecessor-adr-id> --status superseded --related adr=<adr-id>
```

Nothing heals that pair on its own — distill's resume rule walks only distilled ADRs, and this one
came the forward way.

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
- **The adjudicator is a reviewer, not a router.** Consolidation, spot-verification, and
  the single recommendation built out of them are the job. Thirty findings forwarded verbatim is a
  failed adjudication.
