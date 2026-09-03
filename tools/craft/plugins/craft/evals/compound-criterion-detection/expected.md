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
