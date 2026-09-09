# Council synthesis — operating instructions (baseline)

You are the main session, synthesizing a council review. Four members were dispatched in
parallel and two have already returned; their responses are captured in the fixture file you
were given, along with the spec under review and the maturity calibration block rendered for
it. Read the fixture file in full, then perform synthesis on it exactly as the instructions
below direct, and present your consolidated findings list.

The instructions below are `_shared/council.md`'s own Synthesis section, reproduced, less the
cross-reference clauses, at the revision committed before this task's change.

---

## Synthesis (main session, NOT a subagent)

After all four members return:
1. **De-duplicate by issue, not by member.** If two members raised the same finding (e.g.
   Security and Reliability both flag a missing audit log), present it once, grouped by the
   issue, noting which lenses raised it.
2. **Auto-downgrade speculative Criticals.** A Critical that is vague ("this could be a
   problem"), requires guessing about scale / future state / user behavior, or names no
   concrete failure scenario is reclassified Important. State explicitly which findings were
   downgraded and why.
3. **Write the narrative synthesis** — the prose that carries the judgment. This is what the
   reader reads first.
4. **Then present the consolidated list**, grouped Critical → Important → Minor, noting the
   member count behind each multi-lens finding.

---

Only two of the four members are captured in this fixture — proceed with the two you have; do
not invent responses for the other two. Present your final consolidated list in the "Required
output format" the council prompt template uses:

## Findings
- [Critical] <issue>: <one-line what concretely fails>. Suggested: <one-line fix>.
- [Important] <issue>: <one-line>. Suggested: <one-line>.
- [Minor] <issue>: <one-line>.

## Confidence
<one line — low | medium | high, with brief reason>
