# Manual Eval — behavioral gates for craft's own prose

Dev-time acceptance tests for a boundary the pytest suite cannot reach: whether craft's
**agent and skill prose actually changes agent behavior**. A contract test can assert what a
document says; only a run can show what it causes.

Same role as `MANUAL-SMOKE.md`, different boundary — that file covers the plugin-system
boundary (install, agent registration); this one covers the behavioral boundary.

## Why these are run by hand

`claude plugin eval` is the automated harness these cases belong in, and it is **not
runnable for this account**: `claude plugin eval --help` resolves and prints full flag
documentation, but every execution path — `init`, `init --bare`, and a direct case run —
returns `plugin eval is currently in early access` and exits. Verified 2026-09-03 on claude
2.1.259, first-party client, no gating env vars set, already up to date. Enablement is
per-organization with no self-service path.

Each case below is therefore authored in the shape a real eval case reuses verbatim — a
fixture on disk plus a written expected verdict — and dispatched in session until the
harness opens up. Follow-up: `task/give-craft-s-eval-cases-a-recurring-runner`.

## How an arm is dispatched

Both arms point a generic read-only agent at **two file paths**: the agent prose to run as
its operating instructions, and the fixture to run it against. Baseline arm points at the
committed prose; treatment arm points at the edited prose. The arms then differ in exactly
one variable.

**Do not dispatch `craft:<agent>` for the treatment arm.** That subagent type resolves to
the live composed install, not the worktree, so it re-runs unedited prose and reports a
false result. Editing the composed install to work around this is barred by Axiom 6.

---

## Case: compound-criterion detection

`plugins/craft/evals/compound-criterion-detection/` — fixture, and `expected.md` carrying
the pass condition, written before any arm was run.

**Under test:** AC1 of `spec/acceptance-criteria-are-atomic-assertions-a-slice-carries` — a
compound acceptance criterion is detected by the gauntlet's consistency pass and rated
**Critical**, so it takes a disposition and gates the spec's advance to `ready`.

**Pass condition (the full conjunction):** the run names the compound criterion on
*independent-deliverability* grounds, does **not** raise the look-alike conjunction on the
same grounds, and the finding reaches Critical at adjudication. Severity is a second
observation against the gauntlet's adjudication prose — the auditor's output shape carries
no severity field, so the pass cannot rate its own finding.

| Date | Arm | Prose under test | Runs | Result | Notes |
|------|-----|------------------|------|--------|-------|
| 2026-09-03 | baseline | `agents/consistency-auditor.md` @ `60e22bd` | 3 | **RED — 0/3 on the full condition; 1/3 on bare detection** | Fixture 1. See below. |
| 2026-09-03 | treatment | `agents/consistency-auditor.md` @ `e44b49c` | 5 | **5/5 detection — but the fixture was contaminated** | Fixture 1. Recall, not reasoning. See below. |
| 2026-09-03 | treatment | `agents/consistency-auditor.md` @ `e44b49c` | 3 | **3/3 detection under inverted phrasing** | Fixture 2. Generalization confirmed; its negative case was unsound. |
| 2026-09-03 | treatment | `agents/consistency-auditor.md` @ `e44b49c` | 3 | **3/3 on the full anti-heuristic condition** | Fixture 3. Both criteria carry "and"; separated correctly every run. |
| 2026-09-03 | severity baseline | `skills/gauntlet/SKILL.md` @ `e44b49c` | 6 | **Critical 6/6 — already** | 3 neutral + 3 leading prompt. Not red. |
| 2026-09-03 | severity, post-bar | `skills/gauntlet/SKILL.md` @ `843100d` | 3 | **Critical 3/3 — no regression** | Bar now cited by name in the output. |

### Baseline evidence, 2026-09-03

Three independent runs against the same fixture and the same committed prose.

- **Run 1 — caught it, incidentally.** Named AC1 "compound" and split its coverage credit.
  But the reasoning was pass/fail ambiguity — *"if approval succeeds but one subscriber
  isn't notified, is the criterion passed or failed?"* — filed under **untestable criteria**,
  not independent deliverability. A different rule firing on the same sentence.
- **Run 2 — missed it.** Built the full matrix, flagged the email clause as serving no
  objective, and flagged `subscriber` as an undefined term — the undefined noun *inside* the
  compound half — without ever naming the compound structure. Treated AC1 as testable.
- **Run 3 — missed it, and hid it.** Credited AC1 as **full** coverage of the sign-off
  objective and never mentioned the email half at all. Worse than a miss: it records the
  compound criterion as cleanly discharged.

**No run assigned severity, in any arm — structurally cannot.** The auditor's output shape
has seven parts and no severity field.

**Read.** Today's craft does not reliably detect a compound criterion, and when it does, the
catch is a by-product of the testability rule rather than the deliverability question, and
it gates nothing. The failure mode is not silence — it is *inconsistency*, which is worse for
an author, who cannot tell whether a clean pass means the spec is atomic or means they drew
run 3.

**Why the baseline flake does not weaken the gate.** Bare detection at 1/3 would make a
3-run green arm weak evidence, so the recorded pass condition is the full conjunction above,
against which the baseline is a clean 0/3. Treatment arm runs 5, not 3, so the detection
half is measurable rather than assumed.

**Containment.** Fixture is self-contained — zero `[[wikilink]]`s, no cross-reference
resolving to a real vault record. Confirmed by grep before the runs.

### Treatment evidence, 2026-09-03 — and two defects in the test material

The treatment arm took three fixtures to produce a trustworthy result. Both intermediate
failures were defects in the *fixtures*, found by the pass under test. That is worth stating
plainly rather than smoothing over: the check caught bad material twice before the material
was good enough to judge the check.

**Fixture 1 — `spec-compound-criterion.md`. 5/5 detection, and not admissible on its own.**

All five runs named the compound criterion in the new output slot on deliverability grounds,
and all five spared the look-alike. But three runs volunteered that they recognized the
examples: one called the compound criterion "the spec's own worked example... reproduced
near-verbatim", another "the pattern's own worked example".

They were right. The fixture's two signal criteria were the auditor prose's own worked pair
with the nouns swapped, because both were drafted from the same source during the same slice.
Worse, the surface cues correlated perfectly with the answers — the compound one contained
"and", the safe one contained "with". A pass keying on surface form scores 5/5 there having
learned nothing, so **fixture 1 cannot carry the anti-heuristic claim**.

What it does establish, and keeps: the mechanism fires, the output slot is used, and the
severity field stayed absent.

**Fixture 2 — `spec-inverted-cues.md`. Surface cues inverted against the answers.**

The compound criterion was phrased with "with" (the shape fixture 1 taught as safe); the
intended-safe one with "and".

- **Compound criterion caught 3/3.** Detection generalizes. Two runs reasoned explicitly
  against the taught example rather than from it — "unlike the shift-time example, ranking
  isn't a validation guard on the search, it's an additional, independently useful behavior."
  That is the rule being applied, not matched.
- **The intended-safe criterion was flagged 2/3** — and on re-reading, the runs were right
  and the expected verdict was wrong. "A ticket over the limit is rejected, and the engineer
  is shown the reason" *is* separable: the gate ships before the message. Poor, but
  deliverable. It was authored as a clean negative and graded as one; it is a borderline case.
  **Recorded as an authoring error, not a pass failure.**

**Fixture 3 — `spec-inseparable-conjunction.md`. The anti-heuristic's negative direction.**

Built so splitting the safe criterion is *incoherent* rather than merely undesirable, and so
**both** signal criteria carry "and" — surface form therefore carries zero information.

- Safe criterion (claiming assigns the engineer *and* removes the ticket from the unassigned
  queue — one state change described twice): **spared 3/3**.
- Compound criterion (queue shows the owner *and* sends a daily digest): **caught 3/3**.

Two of three runs recorded the negative decision explicitly rather than omitting it — one
under a "Considered and rejected" heading: *"surface 'and,' not compound... the removal is
not separately shippable from the assignment it enacts; one assertion."* A visible negative
decision is what makes this judgment call auditable by an operator instead of trusted.

One run reached the right answer by a route the prose never taught, noting the compound
criterion's two halves serve *different objectives* — "which is itself evidence they aren't
one assertion."

**Read.** Detection generalizes beyond the taught examples, and the anti-heuristic holds when
surface conjunction is decorrelated from the answer. The false-positive lean behaves as
designed: it fires on genuinely borderline cases (fixture 2) and not on inseparable pairs
(fixture 3).

**Limitations, stated rather than buried.**
- Three fixtures, eleven runs, one model tier. Nothing here speaks to other tiers.
- The borderline band is real and unmeasured. Fixture 2's rejected-with-reason case split
  2-1, and no fixture maps where that band begins or ends.
- Fixture 1 contains an accidental contradiction found during these runs — it requires email
  notification under a stdlib-only constraint. Harmless to the conditions under test, and
  left in place: the runs that found it are recorded above, and editing a fixture after it
  has been run invalidates its own evidence.
- **Severity remains unproven: 0/11.** No run has rated anything Critical, because the pass
  structurally cannot. Until Task 4 binds the finding to the Critical bar at adjudication, a
  detected compound criterion still gates nothing — which is the half of AC1 that makes the
  other half matter.

### Severity evidence, 2026-09-03 — the arm that did not go red

The severity half of AC1 is measured against the gauntlet's **adjudication** prose, not the
auditor's, because the auditor's output shape carries no severity field — it structurally
cannot rate its own finding. The arm feeds a captured real consistency-pass report
(`fixtures/auditor-report-fixture1.md`, taken verbatim from a treatment run) to an adjudicator
running the gauntlet skill, and reads back the severity and the advance decision.

**The baseline was not red. Six of six runs rated the compound criterion Critical**, with the
gauntlet prose unmodified — three under a neutral prompt, three under a prompt that named the
finding. The leading prompt was written first, noticed to be leading, and re-run neutrally
rather than kept; both sets agree.

Two things follow, and both correct claims the plan made:

**1. Critical does not gate the advance.** The rule is "a record advances when no Critical
carries a final disposition of `revise`". All six runs dispositioned the compound finding
`resolved` and advanced the spec — and that is the correct outcome, not a leak. `resolved`
means the adjudicator drafts and applies the edit, and every one of the six wrote out the
criterion split before advancing. The compound criterion does not survive.

What the bar actually protects is that the finding **takes a disposition at all**. Important
and Minor take none — they are logged for the audit trail only — so a compound criterion filed
at either severity is noted while the spec advances with it intact. That asymmetry is the
bar's whole justification.

**2. The bar pins behavior rather than adding it.** With the compound check in place and the
gauntlet prose untouched, the finding already reached Critical every time. The behavior
appears to follow from the finding having its own top-level section in the auditor's output
shape — a property an editor could remove without ever seeing this consequence. So the bar is
written as codifying measured behavior, and the record says so. **It is deliberately not
written as fixing a miss, because there is no miss.**

**Post-bar: Critical 3/3, no regression** — and all three runs now cite the bar by name
("Critical by rule"), where the baseline runs reached the same verdict by their own reasoning
each time. That is the intended difference: same outcome, no longer emergent.

**Honest labelling.** This arm is a **no-regression check, not a red-to-green transition**.
The slice's genuine red-to-green evidence is the detection arm above — 0/3 on the full
condition at baseline, 3/3 on the fixture built to defeat memorization.
