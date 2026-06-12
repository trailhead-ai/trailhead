# Circle membership — shared reference

The **circle** is a four-agent review panel. This file is the single source of
truth for who the members are. Both `/forge:consult` and the `planning` skill's
Circle Review step read membership from here and dispatch the agents **directly**
via the Agent tool — neither delegates to the other.

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
You are dispatched as one lens of the circle panel (<lens>). <context-pointers>

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

Paste the matching block into each member's dispatch (the `<lens-critical-bars>` token):

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

## Synthesis (main session, NOT a subagent)

After all four members return:
1. **De-duplicate by issue, not by member.** If two members raised the same finding (e.g. Security and Reliability both flag a missing audit log), present it once, grouped by the issue, noting which lenses raised it.
2. **Auto-downgrade speculative Criticals.** A Critical that is vague ("this could be a problem"), requires guessing about scale / future state / user behavior, or names no concrete failure scenario is reclassified Important. State explicitly which findings were downgraded and why.
3. **Present the consolidated list**, grouped Critical → Important → Minor, noting the member count behind each multi-lens finding.

The planning Circle Review step adds a disposition gate and persists findings into the plan file; `consult` stops here and hands the synthesized view back to the user.
