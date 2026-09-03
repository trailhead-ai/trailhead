# Expected verdict — compound-criterion detection

Written **before** any arm was run, so it cannot be retrofitted to an observed result.

## What is under test

AC1 of `spec/acceptance-criteria-are-atomic-assertions-a-slice-carries`: a compound
acceptance criterion is detected by the gauntlet's consistency pass and rated **Critical**
by the adjudicator, so it takes a disposition and gates the spec's advance to `ready`.

## The fixture

`fixtures/spec-compound-criterion.md`, a self-contained draft spec. Two criteria carry the
signal; the other two and the surrounding sections are ordinary material so the pass has a
real matrix to walk rather than a two-line toy.

**AC1 — genuinely compound. Must be caught.**

> A reviewer can approve a document, and approving it notifies every subscriber by email.

Two independently deliverable assertions. Recording an approval and delivering email to
subscribers ship separately, in different phases, against different surfaces; either can
land and be useful while the other does not exist. A slice can carry one and honestly
report it, leaving the other silently uncovered — which is the failure this spec exists to
stop.

**AC2 — look-alike. Must NOT be caught.**

> A reviewer can change a document's retention date, with validation against the
> workspace's retention policy.

Surface conjunction only. The validation is not separately deliverable: a retention-date
change that skips the policy check is not a partial delivery of this criterion, it is a
different and wrong behaviour. One assertion, stated with its bar.

**The discriminator is independent deliverability, never surface conjunction.** Both
criteria contain "and"-shaped structure. A rule that keys on conjunction flags both, which
is a false positive on AC2 and a failed test — the requirements-engineering literature's
standard atomicity heuristic is conjunction-splitting, and this spec's own definition
explicitly rejects it.

## Pass condition — treatment arm

1. The pass names AC1 as compound, or as carrying more than one independently deliverable
   assertion, in terms that identify it unambiguously.
2. The pass does **not** raise AC2 on the same grounds.
3. The finding reaches **Critical** at adjudication, so it takes a disposition and gates
   the advance.

Conditions 1 and 2 are observed against the consistency pass. Condition 3 is a **separate
observation** against the gauntlet's adjudication prose: the auditor's output shape carries
no severity field at all, so the pass cannot rate its own finding.

## Expected failure — baseline arm

Against craft as committed, the run is expected to **not** satisfy condition 1: today's
auditor has no compound rule. Its verification bar (section 2) asks only "what observation
distinguishes pass from fail?", which AC1 answers perfectly well — an approval is
observable and an email is observable. A criterion can be entirely testable and still be
compound, and that is precisely the gap.

**A run that errors has not gone red.** A malformed fixture path, an unreadable file, or a
dispatch failure is not evidence about the prose. Distinguish the two before recording a
red.

## Stability

**Baseline observed 2026-09-03: 0/3 on the full pass condition above; 1/3 on bare detection.**
The detection half is genuinely flaky — one run named the criterion compound, two did not,
and one of those two credited it as fully covered. Full evidence in `MANUAL-EVAL.md`.

Because bare detection flakes, a 3-run green treatment arm would be weak evidence. **The
treatment arm runs 5.** The gate is the full conjunction, against which the baseline is a
clean 0/3 — no run reached Critical, and the one detection came through pass/fail-ambiguity
reasoning rather than independent deliverability.

A verdict unstable across an arm is not a regression signal, and the instability is itself
the finding — not something to average away.

## Portability

This directory is shaped so a real `claude plugin eval` case can reuse the fixture and this
expected verdict verbatim once that harness is runnable for this account. It is not runnable
today: `claude plugin eval --help` resolves, but every execution path returns
`plugin eval is currently in early access` and exits. Until then the arms are dispatched in
session and the result recorded in `MANUAL-EVAL.md`.

---

# Fixture 2 — `spec-inverted-cues.md`, the generalization test

Written **before** this fixture was ever run.

## Why it exists

Fixture 1 is contaminated. Its two signal criteria are the auditor prose's own worked
examples with the nouns swapped — "a reviewer can approve a submission, and the submitter is
notified" became "a reviewer can approve a document, and approving it notifies every
subscriber by email"; "change a shift's start and end times, with validation against the
store's opening hours" became "change a document's retention date, with validation against
the workspace's retention policy". Three of the five treatment runs said so unprompted, one
calling it "reproduced near-verbatim".

On fixture 1 the surface cues correlate perfectly with the right answers: the compound one
contains "and", the non-compound one contains "with". A pass that keys on surface form scores
5/5 there while having learned nothing. **The anti-heuristic is therefore untested by fixture
1** — which is the one claim the prose most needs to earn.

## The inversion

Fixture 2 swaps the surface cues against the correct answers. Both taught examples are
absent; the domain is unrelated.

**AC1 — compound, phrased like the taught NOT-compound example.**

> Support engineers can search tickets by customer email, with results ranked by the
> relevance model.

Two independently deliverable assertions. Search-by-email alone discharges the spec's whole
stated problem — engineers stop copying emails into a separate tool — and ships useful with
results in any order. Relevance ranking is a separate model-backed deliverable that lands
later. **It uses "with", the surface shape fixture 1 taught as safe.**

**AC2 — NOT compound, phrased like the taught compound example.**

> A ticket over the 25 MB attachment limit is rejected, and the engineer is shown the reason
> it was rejected.

One assertion. A rejection whose reason is never shown is not a partial delivery of this
criterion — it is the failure mode the criterion exists to prevent, and no slice would ship
it deliberately. **It uses "and", the surface shape fixture 1 taught as compound.**

## What each outcome means

- **AC1 caught and AC2 spared** — the rule generalizes. Deliverability is doing the work and
  the anti-heuristic holds under adversarial phrasing.
- **AC1 missed, or AC2 flagged** — the pass is keying on surface form. Fixture 1's 5/5 was
  recall, the anti-heuristic prose is not landing, and it needs rewriting rather than
  shipping. Either error alone is disqualifying; they are not scored independently.

A pass that memorized the two examples fails **both** halves here, so this fixture separates
the two hypotheses cleanly rather than by degree.

## Runs

Three runs. Fixture 1 established that the mechanism fires and the output slot works; what is
in question here is only whether the reasoning generalizes.

---

# Fixture 3 — `spec-inseparable-conjunction.md`, the anti-heuristic's negative direction

Written **before** this fixture was ever run.

## Why a third fixture

The prose makes two claims. Fixture 2 settled the first — a criterion phrased with "with"
can still be compound, caught 3/3. The second claim, **that an "and" alone does not make a
criterion compound**, is still untested, because both earlier negative cases were unsound:

- Fixture 1's negative was the auditor's own worked example with nouns swapped. Sparing it
  proves recall, not reasoning.
- Fixture 2's negative — "a ticket over the limit is rejected, **and** the engineer is shown
  the reason" — was graded as a clean negative and is not one. Two of three runs split it,
  arguing the gate ships before the message, which is correct: a silent rejection is poor,
  but it is deliverable. The runs were right and the expected verdict was wrong. Recorded as
  an authoring error, not a pass failure.

A sound negative needs splitting to be **incoherent**, not merely undesirable.

## The criteria

**AC1 — NOT compound. Splitting it is incoherent.**

> Claiming a ticket assigns it to the claiming engineer and removes it from the unassigned
> queue.

Two descriptions of one state change. The unassigned queue *is* the set of tickets with no
assignee, so "assigns an owner" and "leaves the unassigned queue" are the same event seen
from two directions. There is no slice that delivers one half: a ticket both owned and
unassigned is not a partial delivery, it is a corrupt state. Contains "and".

**AC2 — compound. Must be caught.**

> The queue view shows each claimed ticket's owner, and sends that owner a daily digest of
> the tickets they still hold.

Showing ownership in the queue discharges the spec's second objective on its own. A daily
digest is a scheduled-delivery deliverable in a different phase, useful independently and
easily deferred. Also contains "and" — so **both criteria carry the same surface cue and
differ only in deliverability**, which is precisely the discrimination under test.

**AC3** is a single assertion and should be left alone.

## Pass condition

AC1 spared **and** AC2 caught, in the same run. Because both contain "and", a pass keying on
conjunction flags both and fails; a pass that flags neither has stopped detecting.

## What a failure means

- **AC1 flagged** — the anti-heuristic is not landing; the prose needs rewriting before this
  slice ships, since the false-positive lean would then be firing on inseparable pairs and
  training authors to override the check by reflex.
- **AC2 missed** — detection does not survive a fixture whose criteria are not shaped like
  the taught examples.

## Runs

Three.
