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

**The instructions-file path is a trust boundary — pin it.** The dispatcher resolves that path
itself, and it must always name a **trusted, review-gated, in-repo artifact** (a committed
agent or skill file, ideally at a stated SHA). It must **never** be taken from the fixture, a
spec body, a lore record, a label, or any other value an untrusted party can write. That file
becomes the agent's operating instructions verbatim, so a path sourced from untrusted input is
instruction injection with extra steps — and `claude plugin eval`, which this shape is written
to be reused by, is documented as *not* an OS sandbox: network is unblocked and there is no
path jail. The fixture path is data and may vary; the instructions path may not.

---

## Case: compound-criterion detection

`plugins/craft/evals/compound-criterion-detection/` — fixture, and `expected.md` carrying
the pass condition, written before any arm was run.

**Under test:** AC1 of `spec/acceptance-criteria-are-atomic-assertions-a-slice-carries` — a
compound acceptance criterion is detected by the gauntlet's consistency pass and rated
**Critical**, so it takes a disposition before the spec advances to `ready`.

> **Corrected 2026-09-03.** AC1's own wording says a Critical "gates the advance". Measured
> false — see the severity section below. Critical does not gate; a Critical dispositioned
> `resolved` advances, and that is correct, because `resolved` means the adjudicator drafts
> and applies the fix first. What protects the spec is that the finding **takes a disposition
> at all** — Important and Minor take none. The framing above is corrected here so the
> disproved claim is not the first thing a reader takes away.

**Pass condition (the full conjunction):** the run names the compound criterion on
*independent-deliverability* grounds, does **not** raise the look-alike conjunction on the
same grounds, and the finding reaches Critical at adjudication. Severity is a second
observation against the gauntlet's adjudication prose — the auditor's output shape carries
no severity field, so the pass cannot rate its own finding.

| Date | Arm | Prose under test | Runs | Result | Notes |
|------|-----|------------------|------|--------|-------|
| 2026-09-03 | baseline | `agents/consistency-auditor.md` @ `60e22bd` | 3 | **1/3 detection** | Fixture 1 (contaminated). |
| 2026-09-03 | baseline | `agents/consistency-auditor.md` @ `60e22bd` | 3 | **0/3 detection** | Fixture 3. Run after the correctness review flagged that no baseline existed for this fixture. |
| 2026-09-03 | treatment | `agents/consistency-auditor.md` @ `e44b49c` | 5 | **5/5 detection — but the fixture was contaminated** | Fixture 1. Recall, not reasoning. See below. |
| 2026-09-03 | treatment | `agents/consistency-auditor.md` @ `e44b49c` | 3 | **3/3 detection; failed its own negative case** | Fixture 2. Superseded by fixture 3 — its negative was unsound, and by its own pre-registered rule "either error alone is disqualifying". |
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

**No run assigned severity — structurally cannot.** The pre-change output shape had seven
parts and no severity field; the post-change shape has eight and still none. Severity is the
adjudicator's to assign in either case.

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
- **No arm exercised the criterion unit this slice also delivers.** All three fixtures write
  criteria as `1.` ordered lists, not the `- **ACn.**` bullet form `templates/spec.md` fixes.
  The atomicity *rule* is measured; the *unit* is not. Fixtures are left unedited because
  editing one after it has run invalidates its own evidence — a later fixture should adopt the
  bullet form so the unit is exercised too.
- **AC1's stated verification method is not yet met.** The spec records AC1 as
  *"Verified by: automated assertion"*. This arm is human-triggered, so that obligation is
  outstanding, tracked on `task/give-craft-s-eval-cases-a-recurring-runner`. A slice-loop pass
  reading AC1 as automatically verified would be wrong — which is the half-covered-criterion
  failure this spec exists to prevent, so it is named here rather than left implicit.
- **The severity arm's own limits.** It rests on a single captured pass report, derived from
  the fixture this file calls contaminated, at one model tier, on one finding shape. It shows
  the bar does not regress; it does not establish how severity behaves across finding shapes.
- Fixture 1 contains an accidental contradiction found during these runs — it requires email
  notification under a stdlib-only constraint. Harmless to the conditions under test, and
  left in place: the runs that found it are recorded above, and editing a fixture after it
  has been run invalidates its own evidence.
- **Severity is not observable in this arm: 0/11.** No run rated anything Critical, because
  the auditor's output shape has no severity field and the pass structurally cannot. Severity
  is measured in its own arm, below, against the adjudication prose.

  > **Superseded.** This bullet originally read that a detected compound criterion "still
  > gates nothing" until the Critical bar landed. Both halves proved wrong: the finding
  > already reached Critical 6/6 without the bar, and Critical does not gate the advance in
  > any case. Left visible rather than deleted, because the correction is the finding.

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

**And the headline number, stated correctly.** An earlier draft of this file claimed
"0/3 at baseline, 3/3 after" — that compared the *full conjunction* (which includes reaching
Critical, a condition the auditor arm structurally cannot satisfy) against *detection alone*.
It penalised the baseline by a condition the treatment arm also fails 0/11. The claim was
overstated and is withdrawn.

The defensible comparison is **same fixture, same metric, both arms**:

| Metric | Baseline | Treatment |
|---|---|---|
| Detection, fixture 3 (uncontaminated, cues decorrelated) | **0/3** | **3/3** |
| Detection, fixture 1 (contaminated — recall, not reasoning) | 1/3 | 5/5 |
| Anti-heuristic: inseparable "and" spared, fixture 3 | n/a — no rule to apply | 3/3 |
| Severity reaches Critical | 6/6 *already, before any bar* | 3/3 |

**Fixture 3 is the load-bearing row**: it is the only fixture that is both uncontaminated and
run against both arms, so it is the only clean red-to-green evidence in this file.

**What the baseline actually did on fixture 3** is more interesting than a bare miss. All
three runs noticed the digest clause and filed it under *criteria serving no objective* — a
traceability finding. So the old prose senses that something is off, and files it where it
carries no severity and never says "split this criterion." The gap was never blindness; it
was filing the observation somewhere that does not act on it.

---

## Case: observation-point enumeration

`plugins/craft/evals/observation-point-enumeration/` — fixture pair, and `expected.md`
carrying the pass condition, committed at `c87c857` before any arm was run and before the
prose under test was edited.

**Under test:** AC1 of
`spec/close-the-ritual-gaps-that-produce-rework-the-conformance-gate-cannot-see`, as
relocated by the executor reframe — an executor establishes mechanically where an asserted
property must hold, and **stops** when that enumeration disagrees with the task's declared
`Files:`.

**Pass condition (the full conjunction):** on the underdeclared fixture the run stops
without claiming delivery and names `handlers/exports.py`, and reports the enumeration
command and its count; on the fully-declared fixture the run builds and names no false extra
site. Either half alone is disqualifying.

| Date | Arm | Prose under test | Fixture | Runs | Result | Notes |
|------|-----|------------------|---------|------|--------|-------|
| 2026-09-03 | baseline | `agents/executor.md` @ `129abca` | repo-underdeclared | 3 (1 discarded) | **0/2 on the stop; 2/2 on discovery** | See correction below. One run discarded for contamination. |

### Correction to the pre-registered expected failure, appended 2026-09-03 after the baseline ran

**The pre-registered text in `expected.md` is left unedited on purpose.** Its "Expected
failure — baseline arm" section is **wrong about the mechanism** and right about the outcome,
and the difference matters enough to record rather than quietly absorb.

It predicted: *"the expected baseline behaviour is a correct, well-tested, DONE build of
`refunds.py` that leaves `exports.py` untouched and unmentioned."*

**Measured: both valid baseline runs found `exports.py`.** The committed prose already
produces discovery — Step 1's "read the existing code the task touches" plus the
ambiguous-spec rule in `## Rules` is enough. One run enumerated with reproducible greps
(`grep -n "methods=" handlers/*.py`, `grep -l "validate(" handlers/*.py`) entirely
unprompted.

**What neither run did was stop.** And they diverged in the two worst available directions:

- **Run 2** claimed `DONE`, and **silently widened scope** — it edited `handlers/exports.py`,
  a file outside the declared `Files:`, and added a second test module for it. The extra file
  is flagged in `unknowns`, but the work is already committed against a footprint the task
  never authorised.
- **Run 3** claimed `DONE_WITH_CONCERNS`, left `exports.py` alone, and recommended a
  follow-up — putting the finding into a report field, which the dispatch-lesson corpus is
  consistent that nobody acts on.

So condition 1 fails 0/2, but not for the reason pre-registered. **The gap the reframe closes
is not detection, it is the absence of an authorised stop.** Given no honourable way to halt,
a capable executor either exceeds its footprint or files the finding and proceeds — and which
one you get is a coin flip between dispatches. That divergence is itself a rework generator
and it is a stronger argument for the hard stop than the one the spec makes.

Condition 2 (a reported, reproducible enumeration command) was satisfied **1/2** on baseline
— spontaneously, by run 3 only. **Detection therefore flakes, so the treatment arm runs 5**,
per the precedent in `compound-criterion-detection`.

### Contamination — one baseline run discarded

Run 1 read `plugins/craft/evals/observation-point-enumeration/expected.md` — the answer key —
along with both fixture repos, then disclosed it unprompted and recommended its own result be
discarded. It has been discarded.

The cause is methodological, not agential: **the fixture and its expected verdict live in the
same readable tree as the working directory**, and nothing in the dispatch confined the agent
to the latter. `executor.md`'s worktree-only rule is prose, not a sandbox.

**Prevention, applied to every later arm on this case:** copy the fixture to a scratch
working directory that has no path back to the eval directory, and give the dispatch the
task record's *content* rather than a path into the repo. This is a defect in this file's
"How an arm is dispatched" section as written — it pins the instructions path as a trust
boundary but says nothing about confining the agent's reads — and it applies to any eval
whose fixture ships beside its answer key.

### Treatment arm, 2026-09-03 — 5/5 on the stop, 3/3 on the negative

| Date | Arm | Prose under test | Fixture | Runs | Result | Notes |
|------|-----|------------------|---------|------|--------|-------|
| 2026-09-03 | treatment | `agents/executor.md` (reframed, worktree) | repo-underdeclared | 5 | **5/5 stopped** | `NEEDS_CONTEXT`, `exports.py` named, enumeration command + count reported. |
| 2026-09-03 | treatment | `agents/executor.md` (reframed, worktree) | repo-fully-declared | 3 | **3/3 built** | No false extra site; full mutation transcript per item. |

**The full pre-registered conjunction is satisfied.** All five underdeclared runs
returned `NEEDS_CONTEXT` without claiming delivery, named `handlers/exports.py`, and
reported a reproducible enumeration command with its count. Several named the two
disallowed alternatives explicitly — one wrote that building the narrow scope "would ship
a false 'every write endpoint' claim." All three fully-declared runs enumerated, found the
enumeration agreed with `Files:`, and built. **The stop discriminates on the disagreement,
not on the plural phrasing** — which is what the pair exists to separate, and what
baseline could not do.

Against baseline's 0/2 on the stop with 2/2 detection, the delta is entirely in the
handling, exactly as the correction above predicted.

### Unplanned finding — the mutation-kind and stayed-GREEN rules fired on their own

One fully-declared run produced the strongest single piece of evidence in this cycle,
against a fixture designed to test something else.

It **upgraded** the mutation kind from the default revert to a decoy (schema name
`"refund"` → `"order"`), on its own reasoning that the schema-name string occurs four
times across the handlers. Under that decoy, two contract items **stayed GREEN** — the
wrong schema's own missing-field check incidentally raised `ValidationError`, so the
generic `pytest.raises(ValidationError)` assertions gave false confidence that the refund
schema specifically was being enforced. That is explanation 3, the uncredited third
condition, and it is the case the prose warns is usually mistaken for "defence in depth".

The run then strengthened both assertions to match the exact message, re-ran the same
decoy, confirmed RED for the correct reason, restored with a verified empty diff, and
**reported the upgrade and the stayed-GREEN finding explicitly** rather than quietly
substituting a kind. A second run independently upgraded revert → relocation to bind the
"persists nothing" half on ordering.

Neither behaviour is reachable from the committed prose, which asks only to "break the
behaviour" and reliably gets deletion. This is unplanned evidence and is recorded as such:
no pre-registration covered it, the sample is small, and it should not be cited as a
measured pass condition. It is a reason to pre-register a mutation-kind case, not a
substitute for one.

### Environment note

One treatment run reported that the operator's `rtk` shell hook rewrites
`python3 -m pytest` into a failing invocation (`Failed to spawn process`), and worked
around it with `rtk proxy python3 -m pytest`. Unrelated to the prose under test, but it
means a scoped-suite command named in a dispatch can be broken by operator-local shell
rewriting — worth knowing before reading a stalled run as non-compliance.

---

## Case: the gate reads the evidence artifact

**Outcome: the change this case was built to justify was dropped.** The gate already did
the thing. The case ran three times — once against a fixture that could not test the claim,
once redesigned, and once more after its negative was found unsound — and the section below
keeps all three in order, oldest first, because the sequence is the evidence.

### Final result

| Date | Arm | Prose under test | Fixture | Runs | Result |
|------|-----|------------------|---------|------|--------|
| 2026-09-03 | baseline | `agents/drift-gate.md` @ `origin/main` | summary-only (rev. A) | 6 | **DRIFT 6/6** — predicted PASS |
| 2026-09-03 | baseline | `agents/drift-gate.md` @ `origin/main` | with-transcript (rev. C) | 3 | **PASS 3/3** |
| 2026-09-03 | treatment | `agents/drift-gate.md` (narrowed, worktree) | with-transcript (rev. C) | 3 | **PASS 3/3** |

**What this supports.** Dropping the "tell the gate to open the commit body and confirm the
transcript is there" instruction, and its rationale, and the `Do not reconstruct a missing
transcript` clause. The committed gate opens the artifact unprompted and grades a
summary-as-transcript as DRIFT, 6/6, against a pre-registration predicting the opposite.
Adding that instruction would have codified a fix for a gap that does not exist.

**What this does not support, and must not be read as supporting.** The three clauses that
were kept — the transcript must name which assertion failed, a stayed-GREEN transcript is
complete evidence rather than a gap, and the gate re-runs one observation-point enumeration.
The negative arm returning all-PASS on both prose versions shows only that neither
false-positives on a sound transcript. **Baseline and treatment are indistinguishable on this
fixture, which is the expected outcome** — the negative exists to catch a gate that DRIFTs on
everything, not to demonstrate improvement. The kept clauses remain independently motivated
and behaviourally unmeasured. A case for them needs a fixture where they can fail.

Two treatment runs did exercise them incidentally, which is worth noting but is not
measurement: one cited the stayed-GREEN rule by name in its reasoning, and one verified an
`observation-points: none` claim by searching for call sites rather than accepting it — the
vacuous-pass case that clause was written for.

### On reconstruction — a pre-registered condition that was right for the wrong reason

Pre-registered condition 2 required that a run not reconstruct missing evidence by re-running
the mutations and then passing. It held: on the summary-only fixture the gates reconstructed
**and still returned DRIFT**.

But the framing behind it was wrong. Reconstruction is not a failure mode — it is how these
gates verify anything. Every arm in every run re-ran the mutations independently rather than
trusting the transcript, and on the unsound negative (rev. B) that is precisely what caught a
transcript describing a strengthening absent from the delivered code. A `Do not reconstruct`
clause would have suppressed the gate's single most effective behaviour. It was dropped.

### Fixture revisions — what changed and why it matters for reading the table

- **rev. A** — the original repo-builder output. The summary-only baseline ran here.
- **rev. B** — added the strengthened assertion so the transcript's own claim became true.
  The first with-transcript arms ran here and split 2 PASS / 1 DRIFT.
- **rev. C** — corrected the transcript's line references (18/28 → 21/31) and reconciled
  three disagreeing diffstats (65 / 41 / actual 47) to the real diff.

**The rev. A result stands** — its pass condition turned on the absent transcript, which none
of the later corrections touched — but the fixture it ran against no longer exists, and the
rev. B split was caused by my bookkeeping rather than by either prose version. Under rev. C no
arm spent a finding on fixture metadata.

### Three authoring errors, all caught by the gates rather than by me

1. **The fixture could not test the claim.** The first dispatch handed the gate the commit
   body as a labelled artifact, removing the variable under test. Redesigned as a real
   repository with base and head SHAs so the gate must retrieve the body itself.
2. **The negative was unsound.** Its transcript narrated strengthening a test with an
   assertion that was not in the delivered code. Three gates found it by reproducing the
   mutation. A negative whose correct answer is "this is fine" cannot contain a real defect.
3. **The bookkeeping disagreed with itself.** Three different insertion counts for one commit,
   and line references stale by three. Correct findings, but noise against the variable under
   test — every confound is a chance for a run to reach the right verdict for the wrong reason.

The withdrawal note below is left unedited, as written before the redesign.

---

## Withdrawal note, written 2026-09-03 before the redesign — kept verbatim

`plugins/craft/evals/gate-reads-the-evidence-artifact/` — pre-registered and committed at
`8f0aacb` before any arm was run.

**Under test:** whether `drift-gate` opens the commit body and confirms the mutation
transcript exists, rather than verifying the behaviour the transcript was supposed to
evidence.

| Date | Arm | Prose under test | Fixture | Runs | Result | Notes |
|------|-----|------------------|---------|------|--------|-------|
| 2026-09-03 | baseline | `agents/drift-gate.md` @ `129abca` | commit-body-summary-only | 3 | **DRIFT 3/3 — not red** | Pre-registration predicted PASS 3/3. |

### Why this is an authoring error and not a result

The pre-registration predicted the baseline would **PASS**, on the reasoning that the
committed check 4 points the gate at *the executor's report* — the one channel it cannot
verify — and the fixture's report claims the transcript exists in good faith.

**Measured: all three baseline runs returned DRIFT**, each naming the summary as a bare
narrative claim carrying no per-item evidence. One explicitly observed that the report
"points the gate here, but this document doesn't carry the evidence it promises."

The prediction was not merely wrong; **the fixture cannot test the claim.** The dispatch
handed the gate the commit body as one of four labelled artifacts. The field failure recorded
in `lesson/make-the-gate-verify-the-evidence-artifact-exists-not-just-the-claim-it-evidences`
is about a gate that had to *decide to go and open the commit* and never did. By supplying
the artifact up front, the fixture removed the exact variable under test and measured
something else: whether a gate handed a weak transcript recognises it as weak. It does,
reliably, on the committed prose.

**Status: the case is withdrawn, not failed.** Its files are kept, with this note, because the
fixture pair is sound for a redesigned dispatch — one that supplies a working directory and a
commit SHA and requires the gate to retrieve the body itself. Recorded as an authoring error
per the precedent set by `compound-criterion-detection` fixture 2.

**Consequence for the reframe.** The drift-gate check-4 rewrite currently has **no behavioural
evidence behind it**, and the one measurement taken says the committed prose already handles
the case the rewrite was aimed at. The parts of the gate change that remain unmeasured and
independently motivated — grading a stayed-GREEN transcript as evidence, re-running one
observation-point enumeration, and the `Do not reconstruct a missing transcript` clause — are
not covered by this result either way. Do not cite this case as support for the gate change.
