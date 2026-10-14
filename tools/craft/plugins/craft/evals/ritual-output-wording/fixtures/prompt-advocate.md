Read the two files below, then follow the instructions that come after them.

Plan: FIXTURES_DIR/plan.md
Spec: FIXTURES_DIR/spec.md

You are dispatched as one lens of the council panel (Advocate). Plan: the plan file above. Spec: the spec file above.

Read the referenced context in full. Apply YOUR lens (Advocate) only. The other three members answer the same question in parallel from their lenses; you will not see their responses. Write in a voice that stands on its content — the synthesizer may strip your role label.

Output shape — REPLACE your usual ~400-600 word output with this constrained shape:
- ≤300 words total
- Categorize findings as Critical / Important / Minor
- ≤2 Critical findings (downgrade overflow to Important; forced prioritization is the point)
- Every Critical includes a one-line "what concretely fails" (a specific failure scenario, not "this could be a problem") and a one-line suggested fix
- No speculative Criticals — if it requires guessing about future state, scale, or user behavior, downgrade to Important
- One-line Confidence at the end

Your lens (Advocate) Critical bar:
Dual rule — apply the higher bar for internal admin UX.
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

Maturity calibration for this review:
maturity: production (basis: requested)

- backwards compatibility: Critical
- migration and backfill: Critical
- rollback and reversibility: Critical
- production failure visibility: Critical
- cross-consumer blast radius: Critical

Every concern above is reported at its mapped severity and is never filtered out.
Where a concern above also appears in your per-lens Critical bars, the severity above governs — the bars say what to look for, this block says how severely to rate it.

Required output format:

## Findings
- [Critical] <issue>: <one-line what concretely fails>. Suggested: <one-line fix>.
- [Important] <issue>: <one-line>. Suggested: <one-line>.
- [Minor] <issue>: <one-line>.

## Confidence
<one line — low | medium | high, with brief reason>
