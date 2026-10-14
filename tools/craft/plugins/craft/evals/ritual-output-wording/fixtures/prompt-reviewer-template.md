Read the two files below, then follow the instructions that come after them.

Plan: FIXTURES_DIR/plan.md
Spec: FIXTURES_DIR/spec.md

You are dispatched as one lens of the council panel (Reliability). Plan: the plan file above. Spec: the spec file above.

Read the referenced context in full. Apply YOUR lens (Reliability) only. The other three members answer the same question in parallel from their lenses; you will not see their responses. Write in a voice that stands on its content — the synthesizer may strip your role label.

Output shape — REPLACE your usual ~400-600 word output with this constrained shape:
- ≤300 words total
- Categorize findings as Critical / Important / Minor
- ≤2 Critical findings (downgrade overflow to Important; forced prioritization is the point)
- Every Critical includes a one-line "what concretely fails" (a specific failure scenario, not "this could be a problem") and a one-line suggested fix
- No speculative Criticals — if it requires guessing about future state, scale, or user behavior, downgrade to Important
- One-line Confidence at the end
- This report goes to the agent that dispatched you, not to a person: cite an acceptance criterion by its number alone, as the spec writes it (for example AC5); the output-wording section in your rules does not apply to it

Your lens (Reliability) Critical bar:
- A task has no test contract, OR test contract is vacuous
- New code path's failure mode is invisible — no health check, metric, log, or other signal — with no substantive reason
- Plan removes existing test coverage without replacement
- A task does irreversible work without dry-run / preview / staged rollout
- A destructive migration or backfill runs without a gated, replayable console (the ORM / query layer or migration/backfill console) instead of an ad-hoc one-shot

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
