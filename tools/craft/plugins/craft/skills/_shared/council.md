# Council membership — shared reference

The **council** is a four-agent review panel. This file is the single source of
truth for who the members are. `/craft:consult`, the `planning` skill's Council
Review step, and the `gauntlet` skill's lens pass all read membership from here and
dispatch the agents **directly** via the Agent tool — none delegates to another.

## Members

| Agent | Lens | Reviews for |
|-------|------|-------------|
| `builder`  | Builder     | *How* to build it — architecture, code structure, where it lives, libraries, prior art. |
| `breaker`  | Reliability | How we'll know it works and how it behaves when it doesn't — tests, edge cases, failure modes, recovery, abuse patterns. |
| `attacker` | Security    | Red-team — authn/authz, data handling, secrets, injection, PII, threat model. |
| `advocate` | User Advocate | The end user — UX clarity, error messaging, accessibility, device/platform behavior, where a user gets stuck. |

Each member resolves to `agents/<stem>.md` in this plugin. The stems are exactly:
`builder`, `breaker`, `attacker`, `advocate`.

## Dispatch contract

- Make **four parallel `Agent` tool calls** — one per member — in a single
  message so they run concurrently in **isolated contexts**. Each member answers
  the same question from its own lens and returns only a summary; members do not
  see each other's responses.
- Substitute the lens label (`Builder` / `Reliability` / `Security` / `Advocate`)
  into the per-member prompt.
- The **synthesizer is the main session, never a subagent** — it de-duplicates by
  issue (not by member), groups findings by severity, and notes which lenses
  raised each one.

## Prompt template

The dispatching skill fills the per-skill substitution tokens (`<lens>`, the
context-pointer line, `<lens-critical-bars>`, and `<cross-cutting>`) **before**
sending each member its prompt — never ship a literal `<token>` to a subagent.
`<lens>` is one of `Builder` / `Reliability` / `Security` / `Advocate`; the
context-pointer line is whatever the dispatching skill supplies (a `Plan:`/`Spec:`
pair for planning, a `Question:`/`Context to read:` pair for consult);
`<lens-critical-bars>` is the matching block from "Per-lens Critical bars" below;
`<cross-cutting>` is an optional extra Critical block (planning supplies one;
consult substitutes the empty string).

```text
You are dispatched as one lens of the council panel (<lens>). <context-pointers>

Read the referenced context in full. Apply YOUR lens (<lens>) only. The other three members answer the same question in parallel from their lenses; you will not see their responses. Write in a voice that stands on its content — the synthesizer may strip your role label.

Output shape — REPLACE your usual ~400-600 word output with this constrained shape:
- ≤300 words total
- Categorize findings as Critical / Important / Minor
- ≤2 Critical findings (downgrade overflow to Important; forced prioritization is the point)
- Every Critical includes a one-line "what concretely fails" (a specific failure scenario, not "this could be a problem") and a one-line suggested fix
- No speculative Criticals — if it requires guessing about future state, scale, or user behavior, downgrade to Important
- One-line Confidence at the end

Your lens (<lens>) Critical bar:
<lens-critical-bars>
<cross-cutting>
Required output format:

## Findings
- [Critical] <issue>: <one-line what concretely fails>. Suggested: <one-line fix>.
- [Important] <issue>: <one-line>. Suggested: <one-line>.
- [Minor] <issue>: <one-line>.

## Confidence
<one line — low | medium | high, with brief reason>
```

## Per-lens Critical bars

Two sets ship. Pick by **what is under review**, and paste the matching block into
each member's dispatch (the `<lens-critical-bars>` token):

| Reviewing | Use |
|---|---|
| An implementation plan (`planning` step 8.5) | **Per-lens Critical bars** (below) — phrased at task altitude |
| A draft spec (`gauntlet` lens pass) | **Per-lens Critical bars — spec review** (further below) — phrased at spec altitude |
| A standalone question (`consult`) | The plan bars, read as applying to the unit under review; skip any bar with no analogue |

The sets are **not** interchangeable. A spec has no tasks, no test contracts, and
no code paths yet — running the plan bars against a spec produces findings that
are all technically true and all useless ("this task has no test contract" — there
are no tasks). The spec bars fire on what a spec can actually get wrong instead.

*Builder:*
- Task ordering creates a dependency that can't be tested
- Architecture choice contradicts a declared axiom in the plan
- Producer task's contract isn't proven by tests but a consumer task depends on it
- Plan introduces a new abstraction layer for a single caller (premature)

*Reliability:*
- A task has no test contract, OR test contract is vacuous
- New code path's failure mode is invisible — no health check, metric, log, or other signal — with no substantive reason
- Plan removes existing test coverage without replacement
- A task does irreversible work without dry-run / preview / staged rollout
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

## Per-lens Critical bars — spec review

Used by the `gauntlet` skill's lens pass. A spec is reviewed for what it *commits the
project to*, not for how it will be built — the lenses here fire on objectives,
acceptance criteria, Required Interfaces, non-goals, and constraints.

The four lenses accept the spec's framing and review within it. Attacking the framing
itself is the `premise-attacker`'s job, not a lens's — a lens finding of the form "this
is the wrong problem" belongs to that pass and should not be raised here.

*Builder — spec review:*
- An objective has no implementable reading — no build satisfies it as stated
- The spec mandates an approach that contradicts a declared project axiom or a prior decision record
- The spec depends on a capability that does not exist yet and does not name that capability as a dependency the build must satisfy — the defect is the missing name, not the missing capability
- The spec requires a new subsystem where an existing one already covers the need

*Reliability — spec review:* (criterion **testability** and objective **coverage** belong to the `consistency-auditor` pass — do not raise them here; stay on failure behavior)
- A failure mode named in the Problem has no criterion proving it is addressed
- The spec commits to something irreversible (a migration, a deletion, a published contract) without naming the rollback or migration path
- The spec defines no behavior for a state the system will certainly reach — empty, partial, concurrent, or interrupted — a state-coverage gap: a state no acceptance criterion covers, distinct from the criterion-coverage matrix the consistency-auditor pass already builds
- When this fails in production, the spec names no signal a human would see; the first reporter is a user

*Security — spec review:*
- The spec introduces a trust boundary it never names
- Untrusted or externally-influenced input enters the system with no named handling at the data boundary
- The spec stores, logs, or transmits sensitive data without naming its classification, retention, or redaction
- An authorization model is implied by the objectives but never stated

*Advocate — spec review:*
- A primary user flow, as specified, has a reachable state with no way out
- The spec names a user-facing surface but gives no direction for its error or empty states
- Success is defined only in system terms, with no outcome a user would notice
- The UI Direction contradicts an acceptance criterion

## Synthesis (main session, NOT a subagent)

After all four members return:
1. **De-duplicate by issue, not by member.** If two members raised the same finding (e.g. Security and Reliability both flag a missing audit log), present it once, grouped by the issue, noting which lenses raised it.
2. **Auto-downgrade speculative Criticals.** A Critical that is vague ("this could be a problem"), requires guessing about scale / future state / user behavior, or names no concrete failure scenario is reclassified Important. State explicitly which findings were downgraded and why.
3. **Write the narrative synthesis** — the prose that carries the judgment, in the shape "How the synthesis reads" defines below. This is what the reader reads first.
4. **Then present the consolidated list**, grouped Critical → Important → Minor, noting the member count behind each multi-lens finding, and writing each finding in the shape "How a finding reads" defines below.

The planning Council Review step adds a disposition gate and persists findings into the plan file; `consult` stops here and hands the synthesized view back to the user.

(The `## Council Review` section `plan` persists into the plan file is a separate, one-line-per-finding record schema for a file rather than for someone reading in session; `plan/SKILL.md` owns its shape, and neither the narrative shape nor this contract governs it. The carve-out is that one persisted section — what `plan` *presents* to the user in session is governed here like any other caller's.)

### How the synthesis reads

The consolidated list is not the deliverable. A list makes the reader reconstruct how the findings relate to each other, which of them matter, and why, by reading across rows — and a table does it no better than a bulleted list does. The narrative is where that reconstruction has already been done for them, so it leads, and the list below it is **supporting detail** rather than the place the explanation lives.

Three movements, one short paragraph each, in this order:

1. **What the passes found** — the themes they collectively landed on, written **in the reviewed record's own terms**: its objectives, its criteria, its named parts. Themes, not a paragraph per finding.
2. **Whether it holds, and where it came from** — your own read on validity and provenance: which passes were right, which overreached, and which raised something that is an **artifact of the lens rather than a defect** in the record. This is an **interpretive per-pass account, and it is wanted** — it is how the reader judges the findings without re-deriving them. What is forbidden is the **mechanical roll-up** — "premise raised 2, breaker raised 3" — a count wearing a sentence's clothes, which tells the reader nothing they can act on.
3. **What to do about it** — the remedy stated whole, as one plan, rather than distributed a clause at a time across the rows below.

Someone who wrote the record under review last week should be re-oriented, and should know what you are recommending and why, from these three paragraphs alone. If the three will not fit in three short paragraphs, **the finding set was under-consolidated** — that is a signal to go back to de-duplication, not to spend more of the reader's attention.

### How a finding reads

Every finding in that consolidated list — the one presented to the user in session — takes this shape. Someone who has not read the document under review should understand the Critical list on the first pass.

- **Headline first.** One line, everyday words: what goes wrong, and for whom. It comes *before any mechanism* — no setup, no citation, no shorthand.
- **Then the mechanism.** A short paragraph: what the document says, what reality does, and the concrete worst case, stated as an event ("we delete a workspace with a live session still in it").
- **Then the fix, on its own line**, prefixed `Fix:` — one sentence for the remedy, plus any caveat that changes whether to take it.
- **Shorthand last, if at all.** Pass names, invariant tags, flag trivia and other internal labels belong only after the plain statement, and only when the reader needs them to act. Which lens or pass raised a finding rides beside it, not inside its body.

If a finding only makes sense to someone who has already read the document, rewrite it.
