---
name: gauntlet
description: >
  Run the adversarial review — the gauntlet — on a draft spec before it advances. Eight parallel
  passes attack it from independent angles: fact verification, premise attack, the four council
  lenses, an internal-consistency audit, and a plan-divergence probe. The main session adjudicates
  and hands back one compact recommendation — a synthesis, a recommended outcome, and a proposed
  disposition per Critical — which the user accepts or overrides in a single round-trip, before
  the record is stamped with review provenance and the spec flips to `ready`.
  TRIGGER when: brainstorming has produced a spec and is at its exit gate (the gauntlet is a
  mandatory step there), or the user says "run the gauntlet", "gauntlet this spec", "adversarial
  spec review", "review the spec before it advances", or invokes /craft:gauntlet explicitly.
  DO NOT TRIGGER when: reviewing an implementation plan (planning's Council Review step covers that),
  reviewing written code (use review), or the spec is already `ready` — a settled record is not
  re-gauntleted; new thinking creates a new spec instead. An adr record never triggers this skill,
  at any status — distill is the only path that authors and activates one, backward from an
  already-completed spec, and its own disposition owns the flip.
---

# The spec gauntlet

A spec is the most expensive artifact in the pipeline to get wrong. Every plan, every slice, and
every line of code downstream inherits its mistakes — and by the time the mistake is visible, it is
load-bearing. The gauntlet is the last point where the spec is still cheap to change.

**This is a refining step, not a braking one.** The gauntlet exists to sharpen an idea against
everything that already constrains it — the corpus of what the vault records, the decisions already
made, the code that actually exists, and the realities of the world the spec has to survive in — so
the version that reaches planning is the one worth building. Its output is a better spec and more
momentum, not a delay to be endured; a pass that only slows the record down without changing it has
failed at its job. Treat the findings as the fastest available route forward, because they are: the
alternative is discovering the same things after they are load-bearing.

Eight passes attack the spec in parallel, each from an angle the others structurally cannot see. The
main session adjudicates what comes back and turns it into one recommendation. Nothing advances
until the user has accepted that recommendation.

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
the spec it just wrote. Read it **in full** (`lore record show <spec-id>`). Confirm its **kind** is
`spec`; any other kind resolved here — an adr, a task, a lesson, or any other record — is turned
away before anything else runs, naming the kind found so the operator knows what was rejected. An
adr specifically routes to distill: it is the only path that authors and activates an adr, backward
from an already-completed spec, so route it there instead of proceeding with a review this skill has
no shape for. Confirm its status is `draft`; a `ready` spec has already advanced and is not
re-gauntleted (see brainstorm's Status Lifecycle — new thinking creates a new spec instead).

**Resolve its absolute path too** (`lore record show <spec-id> --json` carries it; `lore search`
prints it). The passes run in isolated contexts and most of them have no `Bash` — they open the spec
with `Read`, so an absolute `<spec-path>` is what you hand them, exactly as planning's Council
Review hands the council its `Spec:` pointer. Never ship a bare record id to a pass that cannot
resolve it.

**Certify criterion content before dispatch.** Pipe the spec body through the criterion gate now,
before any of the eight passes are dispatched — a spec that will refuse must not first burn eight
parallel agents:

```sh
lore record show <spec-id> | ${CLAUDE_PLUGIN_ROOT}/scripts/criterion_gate.py
```

Exit 0 certifies — proceed to step 2. **Exit 2 with `reason-code: zero-criterion-identifiers` is the
one non-zero exit that proceeds** — the legacy carve-out for a spec predating the `**ACn.**`
identifier convention. When it fires, report to the operator, before proceeding to step 2, that this
run's criterion-content bars were not applied — never let the spec reach the passes
indistinguishably from a certified one.

Every other non-zero exit refuses the run here, in the same refusal shape this skill's other guards
already use: name the criteria at fault and the offending span or trailer problem from the gate's
own `reason:` line, name the remedy its `reason-code:` line identifies, and stop — the spec stays
`draft`, nothing is dispatched, and no writes land. This covers exit 1 (integrity violation — a
refused implementation-identifier span, or a missing or unsanctioned verification trailer) and every
other exit-2 reason — `reason-code: duplicate-acceptance-criteria-heading`,
`reason-code: unterminated-masked-region`, `reason-code: empty-stdin`,
`reason-code: non-utf8-stdin`, or no `## Acceptance Criteria` heading at all (no reason-code of its
own). None of these is the carve-out above, and none of them proceeds.

**Named limits.** The gate inspects inline code spans only — a criterion naming an implementation
detail in bare prose, with no surrounding backticks, is a false negative it accepts. A spec
predating the `**ACn.**` convention takes the carve-out above and is never subject to either bar.
And naming a status or error value by its role, per the template's guidance the spec author
followed, trades away a criterion's ability to pin that value's exact literal wire spelling — when
the literal spelling itself is the contract, a human still maps role to literal when writing the
assertion.

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
quietly run seven and present the result as a gauntlet. A review that silently lost its premise pass
is worse than no review, because it still ends in a `ready` spec. Name the missing agent, tell the
user to install it, and leave the spec at `draft`.

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

**Passes 3–6 — the four lenses.** Dispatch per the dispatch contract in `_shared/council.md` — read
it; do not re-inline the roster, prompt template, or bars here. Fill the substitution tokens
**before** sending each member its prompt (never ship a literal `<token>`):

- the context-pointer line →
  ```text
  Review the draft spec against your lens.
  Spec: <spec-path>
  ```
- `<lens-critical-bars>` → the matching block from **"Per-lens Critical bars — spec review"** in
  `_shared/council.md`. **Not** the plan bars — a spec has no tasks, and the plan bars fire on
  things that don't exist yet.
- `<cross-cutting>` → the empty string (the plan-drift block is planning-only).

**Passes 7–8 — consistency audit and divergence probe.** Dispatch `consistency-auditor` and
`divergence-prober` with the spec path. Their prompts are self-contained; they need no extra
framing.

### 4. Adjudicate (main session, NOT a subagent)

Eight passes return on the order of thirty raw findings. **Handing that list to the user is not
adjudication — it is delegation of the work you were dispatched to do.** Consolidate first:

1. **De-duplicate by issue, not by pass.** Two of the passes reaching the same finding from
   different angles is one finding, annotated with both.
2. **Weight cross-pass convergence.** When independent passes — which could not see each other's
   work — converge on the same issue, that is the **strongest severity signal available to you**.
   Rank convergent findings above single-pass findings of nominally equal severity.
3. **Drop the editorial.** Wording preferences, section-ordering suggestions, and style notes are
   not spec defects. Cut them silently.
4. **Auto-downgrade speculative Criticals**, per the synthesis rules in `_shared/council.md`. State
   which and why.
5. **Rate a criterion the consistency pass reports as compound Critical.** Not one it merely reads
   as long or complex — the compound-criteria section of the auditor's output shape (its item 3),
   naming an assertion the auditor judged independently deliverable. Important and Minor take no
   disposition — they are only logged for the audit trail — so a compound criterion filed at either
   severity is noted and the spec advances with it intact. Critical is what forces a disposition:
   `resolved` means the adjudicator drafts and applies the split before the record advances, which
   is what keeps the compound criterion from surviving. This bar pins measured behavior, not new
   behavior — under the auditor's current output shape a reported compound criterion already reaches
   Critical here. Write it as a rule so an edit to that output shape, or to this step, does not
   silently regress it. **This bar is not subject to item 4's auto-downgrade.** Independent
   deliverability is a judgment about how work would be sliced, which can read as "guessing about
   future state" — but it is answered from the criterion's own text, and downgrading it to Important
   strips the disposition that makes the finding land at all.
6. **Hold every `revise` finding to the prescription-specificity bar.** A finding that cannot
   produce a prescription this specific is not a Critical — a severity bar applied here, at
   consolidation, before a finding is numbered, not a step-5 consult. `dispositions.md`'s `revise`
   glossary entry restates it beside the term it defines, downstream of this decision.
7. **Spot-verify contentious claims.** Any finding that would be expensive to act on, that
   contradicts another pass, or that arrives in a transcript reading anomalously (over-confident,
   thin on evidence, or wandering outside its stated lane) gets checked yourself before it reaches
   the user. Do not launder an unverified subagent claim into a recommendation.
8. **Number the surviving Criticals `C1`…`Cn`.** Assign the ids here, at consolidation, in the order
   you will present them. An id is **stable for the rest of the run** — the operator names it to
   override, and the audit trail records it — so never renumber after presenting, not even when an
   override collapses a row's relevance.

Consolidation is not the deliverable. What the operator sees is the recommendation step 5 builds out
of it.

### 5. Recommend, then accept

The operator's job here is to **decide, not to re-derive**. A finding dump makes them re-open the
record to judge each item; a recommendation lets them judge in place. So the default output is one
compact deliverable — target one terminal screen (~40 lines) for a typical run — and one word ("go")
is a complete answer to it.

The deliverable is these four parts, in this order — all four whenever there is a Critical to
disposition, and the same parts minus the table on a run that produced none (`dispositions.md`'s
"Zero Criticals is still a decision"):

1. **The narrative synthesis** — prose, and the part that carries the deliverable. Three movements,
   one short paragraph each — what the passes found, whether it holds and where it came from, and
   what to do about it — written in the shape **"How the synthesis reads"** defines in
   `_shared/council.md`. Read it there; do not restate it here. Two things about it are worth naming
   for this caller in particular:

   - **No sentence cap.** Three movements do not fit a fixed sentence count, and a cap tight enough
     to squeeze them squeezes two of the three down into the table instead — which is the reader
     reconstructing the explanation from rows again. What holds the length is the one-screen budget
     above, plus the shared rule that a synthesis which will not fit means the finding set is
     **under-consolidated**: go back to step 4 rather than spend more of the screen.
   - **The interpretive per-pass read is the point.** Eight passes attacked this record from lanes
     that could not see each other, and which of them were right is a judgment only the adjudicator
     is positioned to make. Making it is movement two's whole job; the shared contract names what
     stays forbidden.
2. **The recommended outcome**, on its own line — whether the record advances this round, or which
   revise round it continues into (`dispositions.md`'s "Advancing, revise rounds, and runs").
3. **The per-Critical table** — supporting detail, not the explanation. The synthesis has already
   said what the findings mean and what you propose doing; the table is the row-level view for
   checking that against the findings, and the handle the operator names to override — into
   `accepted-as-risk`, `disputed`, or `answered` when the operator holds a counterargument the
   passes did not have. One row per Critical, in `C1`…`Cn` order:

   | id | finding | proposed disposition | proposed edit |
   |---|---|---|---|
   | C1 | *headline, one line* | `resolved` | *one clause: what the edit does* |

**A `revise` row cannot fit in that one-clause edit cell** — its prescription names what is wrong,
what to change, and how, and a `reaches-downstream` prescription also names the specs it
invalidates. Render it instead of a table row, as a compact header line followed by an indented
prescription block:

   ```
   **C2** — *headline, one line* — `revise` (`record-only`)
     Prescription: <what is wrong, what to change, and how>
   ```

A `reaches-downstream` prescription's block adds the specs it names:

   ```
   **C3** — *headline, one line* — `revise` (`reaches-downstream`)
     Prescription: <what is wrong, what to change, and how>
     Reaches: spec/<name-one>, spec/<name-two>
   ```

Worked example — three Criticals, one `resolved`, one `revise` of each scope:

   <!-- worked-example:start -->
   | id | finding | proposed disposition | proposed edit |
   |---|---|---|---|
   | C1 | *cache TTL undocumented* | `resolved` | *add a Constraints row stating the TTL* |

**C2** — *auth model assumes single tenant* — `revise` (`record-only`) Prescription: rewrite the
Objectives to state multi-tenant is out of scope, or the criteria to require tenant scoping.

**C3** — *retry policy duplicates existing library behavior* — `revise` (`reaches-downstream`)
Prescription: drop the custom backoff loop; the existing HTTP client already retries with jitter.
Reaches: spec/retry-policy-for-the-notification-worker
   <!-- worked-example:end -->

The block stays inside the one-screen budget the deliverable is held to — one header line and at
most two prescription lines per `revise` Critical, same as the one-clause edit it replaces for every
other disposition.

4. **Important and Minor, compressed** to a count plus a one-line theme apiece. They take no
   disposition — they are logged for the audit trail.

**Draft every `resolved` edit in full before you present.** The table's edit clause summarizes text
you have already written, and acceptance applies that text verbatim. An accepted recommendation must
never send you back to compose the edit you promised, because what would land then is text the
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

The agent proposes **only `resolved` or `revise`**. Both are judgments about the document, and the
adjudicator has read every pass that attacked it.

**Before proposing `revise`, apply the edit-first test.** Ask whether any set of edits to the
record's own sections would answer the finding. If yes, the disposition is `resolved` and drafting
those edits is the adjudicator's job — the test is applied, not skipped because the finding sounds
severe. `revise` is reserved for a finding an edit here cannot answer: the record's framing is
wrong, or the fix reaches beyond this record's own sections. State the asymmetry plainly: a finding
of the form "the record does not say X" is never a `revise` — at most it is a missing edit, and the
disposition is `resolved`; only a finding that a drafted edit cannot answer on the spot is a
`revise`. **Proposing `revise` for a finding an edit would answer is the expensive error** — it
defers a fix the adjudicator could draft right now, and hands the operator a prescription for work
that was already done, none of which a missed line ever required.

`accepted-as-risk: <reason>`, `disputed: <reason>`, and `answered: <reason>` are **operator-only
overrides**. All three are judgments about what this project is willing to live with, or knows that
the passes did not, and their reason text is the operator's own — quote it, **never drafted for
them**. Do not propose any of the three, and do not offer a reason the operator did not say.

**Read `gauntlet/dispositions.md` now and follow it in full.** It defines what each of the five
terms — `resolved`, `revise` and its two scopes, `accepted-as-risk`, `disputed`, and `answered` —
means and requires. The propose-only rule, the edit-first test, and the never-draft-the-operator's-
reason rule above stay inline because this skill must apply them unprompted, not by accident —
`dispositions.md` holds the rest.

#### Advancing, revise rounds, and runs

**Read `gauntlet/dispositions.md` now and follow it in full.** It defines when a record advances,
the run/revise-round distinction, why a revise round still lands its `resolved` edits and
provenance, and why `answered` blocks the advance check until re-adjudicated. The `revise`
prescription and `reaches-downstream` above, the prescription-specificity bar in step 4, and step
6's advance condition below stay inline — each fires unprompted, not by accident.

#### Zero Criticals is still a decision

**Read `gauntlet/dispositions.md` now and follow it in full.** It defines why a clean run still
presents the deliverable — minus the per-Critical table, since there are no Critical rows to hold
one — and still gates on operator acceptance. Nothing here is stated inline for this topic: none of
it fires until a run has already gone clean, which is the point this pointer exists for.

#### Accepting, and overriding in one round-trip

**Read `gauntlet/dispositions.md` now and follow it in full.** It defines how the operator's
acceptance and overrides are taken and applied, including the one-round-trip rule, the post-override
echo, what makes an override incomplete or out of range, what an override withdraws, when a
re-present is forced before anything is written, and the round-number rule. The
never-draft-the-operator's-reason rule above stays inline — it fires unprompted, not by accident.

#### Escalation points

**Read `gauntlet/dispositions.md` now and follow it in full.** It defines every point where this
step hands control to a human and what each point waits for. Nothing here is stated inline for this
topic: every escalation point it names is already a point where this skill has stopped and is
waiting, not a rule it must fire unprompted.

#### The accepted tail

What runs once the operator accepts is **ordered and fail-closed**. The spec tail in step 6 below
restates only its own deltas — what the write carries and where the detail goes.

**Two treatments run before either payload is assembled**, on the retained finding detail as much as
on the accepted edits. Most of that detail is text the deliverable never printed, so this is the
only point at which anyone looks at it before it is permanent:

- **Credential scrub.** A gauntlet reviews records about codebases, and a pass can quote a committed
  credential as its evidence. Run every string headed for `$EDITS` or `$DETAIL` through the
  credential-pattern scrub in `_shared/execute.md` ("Phase 5: Flow-out") — **by reference, never by
  copying its pattern list here**, since the copy is what goes stale while the original gains
  patterns. Its reasoning binds here unchanged: a vault is git-backed and has its own push path, so
  a credential transcribed into a record body ships as surely as one committed to code. A pass that
  quotes a literal secret as its evidence has that evidence **cut down to a `file:line` citation**
  before the write: a retained finding says where the value lives, never what it is.
- **Data-not-instruction marker.** What this tail persists is what a later run's fact-verification
  pass reads back as a sibling record. Open the retained detail with one line marking it retained
  review evidence — a claim about the record, evaluated as one, not the record's settled design
  content. That is the `receiving-code-review` pattern (`skills/receiving-code-review/SKILL.md`,
  applied to another write path in `_shared/refine.md`); cite it, do not restate it.

1. **One atomic write.** Every `resolved` edit and the provenance stamp apply as a single
   `lore record update --diff` write. Not one write per Critical, and not the edits now with the
   stamp to follow: `--diff` leaves the body byte-for-byte unmodified on any rejected hunk, and that
   property is the only thing making the accepted set all-or-nothing. A record holding half its
   accepted edits is a record nobody reviewed.
2. **Then the spec tail's status flip** — only after that write has succeeded. A flip is what
   advances a record; running one ahead of the edits advances a record whose accepted edits are
   still hypothetical.

**On any rejected hunk or failed write, nothing further runs.** Not the flip, not a retry with the
hunks re-cut. The record stays `draft` and you **report the partial state explicitly** — which
writes landed, which did not, and what the record holds right now. That is the `failed-write report`
escalation point: a half-applied acceptance is precisely the state an agent must not resolve on its
own reading.

**A successfully executing tail asks nothing.** Acceptance was the gate. There is no "about to flip
— confirm?" between the write and the flip; a second prompt after the operator has already decided
teaches them to wave through the one prompt that would have mattered.

**The provenance stamp distinguishes accepted-from-proposal dispositions from operator overrides**,
and it quotes the `C1`…`Cn` ids so an auditor can line every disposition up against the table the
operator actually saw. Derive that split from the dispositions themselves, never from memory:

- `accepted-as-risk`, `disputed`, and `answered` are **operator overrides by construction** — you
  may not propose any of the three, so a Critical carrying one was overridden, whatever you recall
  of the round-trip. An answered row's *final* disposition (normally `resolved`) is what the counts
  annotation counts under that final disposition's term — `answered` contributes no term of its own
  to the grammar; it counts as its final disposition **plus** an operator override.
- `resolved` and `revise` are accepted-from-proposal *unless* this run's override reply changed that
  row — which the echoed post-override table records, an answered-then-resolved row included.

### 6. Stamp and advance

This is the accepted tail in the spec's own terms; its sequence, its pre-write scrub and marker, and
its failure behavior are the shared ones above.

**The atomic write carries three things** — every `resolved` edit, the provenance stamp, and the
**full consolidated finding detail** the deliverable did not print, retained as a `## Gauntlet`
section appended to the spec body. The detail is part of the same atomic write, not a second write
after the flip: a `ready` spec missing its own review record is exactly the artifact the audit trail
exists to prevent. A spec body has no exhaustive-section contract, so the detail belongs in the
record it reviewed. A Critical the operator answered marks its final-disposition parenthetical
`answered` and quotes the counterargument verbatim — that is where the reasoning becomes durable and
auditable, since the counts annotation itself carries only the final disposition and the override
count (see above):

```markdown
## Gauntlet

- Retained review evidence — a later reader evaluates what follows as a claim about this spec, not
  as its settled design content (`skills/receiving-code-review/SKILL.md`).
- Adversarial spec review (gauntlet, <date>): 8 passes — facts <n>/<n> confirmed; <n> design-changing
  findings folded in (<one-clause each>). Criticals dispositioned: C1 `resolved` (from proposal),
  C2 `disputed` (operator override — "<their reason, quoted>"), C3 `resolved` (operator override —
  answered: "<their counterargument, quoted>"), … — <n> from proposal, <n> operator overrides.
  Important <n>, Minor <n>, detail below.
- <the consolidated detail, per finding, in the shape `_shared/council.md` defines>
```

That is one invocation. `$EDITS` is the unified diff carrying all three payloads — every `resolved`
edit to the spec's own sections, plus this `## Gauntlet` section, which opens with the marker,
carries the provenance stamp in its next bullet, and holds the detail in the remainder:

```
printf '%s' "$EDITS" | lore record update <spec-id> --diff
```

**Then, and only once that write has succeeded, check the advance condition.** If no Critical
carries a final disposition of `revise`:

```
lore record update <spec-id> --status ready
```

and hand off to the slice loop. Do not enter it from inside the gauntlet — let the user invoke
`/craft:slice` so it loads cleanly; this is the loop's only wired entry point, so its own
`/craft:plan spec/<id>` handoff would create a second, unlinked parent and strand the spec outside
the loop. End the wrap-up with the handoff command **fully formed** — the real spec-id, never a
`<placeholder>` (e.g. `/craft:slice spec/streaming-export`) — so the user can paste it into a fresh
session as-is.

**If any Critical's final disposition is `revise`, the spec does not flip.** Its `resolved` edits
and this round's provenance already landed in the write above — a surviving `revise` withholds only
the advance, never the writes. Begin the next revise round: re-run only the passes that raised the
surviving `revise` Criticals, fold their findings back into step 5, and present again. The spec
stays `draft` for as long as any Critical carries `revise`; there is no round cap, and this skill
writes no status for "still revising."

## Calibration

**Read `gauntlet/calibration.md` now and follow it in full.** It holds the provenance and tuning
notes from the pilot runs that established this protocol. It is consulted after a run, not applied
unprompted during one, which is why none of it is restated here.
