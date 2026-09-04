# Fixture: live legacy draft spec — spec/trailhead-test-suite-audit-and-pruning

Captured verbatim from a real draft-status vault record that carries an
`## Acceptance Criteria` heading but declares zero `**ACn.**` identifiers — the U1 corpus
measurement found this the norm across the vault's 45 draft specs (26 carry the heading,
none declare identifiers). It must take the legacy carve-out.

> **Seed.** Scope and method are settled (below); the rest needs its own brainstorm pass
> before this leaves `draft`.

## Problem

Selection makes execute run fewer tests; it does nothing about what the suite contains.
The trailhead suite is 6,173 tests / 10m50s, and its cost is concentrated rather than
spread: camp and lore hold 278 of the 342 subprocess invocations and 39 of the 44
`time.sleep` calls, while craft (809 tests in 3.7s), outpost, portage, and ranger are
nearly free. The three slowest tests in the suite — roughly 98s combined, 15% of total
runtime — are all camp, all waiting on kill/confirm timeouts. Separately, an unknown
share of the suite is one-time value: proofs that a migration happened or that a seam was
removed, which no longer defend anything.

## Objectives

<!-- Needs grilling. Direction: a defensible verdict on the expensive tail, and a
measured reduction in suite wall-clock that does not cost regression coverage. -->

## Acceptance Criteria

Settled so far:

- **Cost-ranked, not uniform.** Rank by measured cost (`--durations` plus sleep and subprocess density), triage the expensive tail against the rubric, and stop when remaining candidates are cheap. Explicitly does not read the cheap suites — craft's 809 tests run in 3.7s and have nothing to gain.
- **Rubric verdict per test:** `KEEP` (enduring behavioural value) · `COLLAPSE` (duplicate or parameterizable) · `SPEED` (real sleep or subprocess that can become a fake clock or an in-process call) · `DELETE` (one-time migration or removal proof with no regression risk).
- **Output:** a report plus one task record per actionable cluster — this spec produces triage, not code changes.
- **Judgment, not grep.** A name-pattern sweep for removal-shaped tests returned 118 hits across 65 files, and nearly all were legitimate absence-*behaviour* tests (`test_absent_areas_dir_also_safe`). Even the obvious candidate defends itself: `test_seam_removal.py` is 2 tests pinning an architectural axiom, which is enduring value. Mechanical detection can rank cost; it cannot assign the verdict.

<!-- Still to grill: the stopping rule ("cheap" needs a number), who adjudicates DELETE,
whether SPEED rewrites land in this effort or as downstream tasks, and whether a
suite-wall-clock budget becomes a standing check. -->

## Non-Goals

- Test selection at execute time — separate spec.
- Parallelizing the suite — separate task record.
- Auditing repos other than trailhead.

## Constraints

<!-- Needs grilling. Known: deleting a test must not be justified by "it never fails";
that requires mutation evidence, which the repo's TDD rules already demand. -->

## UI Direction

n/a — analysis and task records; no user-facing surface.

## Open Questions / Risks

- **Deletion is irreversible in effect** — a removed regression guard is invisible until the regression ships. Needs a bar for `DELETE` that is stronger than "looks one-time".