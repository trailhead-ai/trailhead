# Fixture: live-shape spec — spec/acceptance-criteria-are-atomic-assertions-a-slice-carries

Captured verbatim from the real vault record's own `## Acceptance Criteria` section — the
spec that introduces the AC3/AC4 bar must pass it. This fixture carries only that section
(plus a minimal wrapper); the rest of the record is out of scope for this gate.

## Acceptance Criteria

- **AC1.** Given a spec containing a compound acceptance criterion, a `/craft:gauntlet` run
  raises it as a **Critical** finding, so the spec cannot advance while it stands. An
  Important or Minor finding does not satisfy this criterion: those take no disposition and
  do not gate the advance, so the spec would flip to `ready` compound-intact.
  *Verified by: automated assertion.*
- **AC2.** Given a `ready` spec, a `/craft:slice` run records on the parent task the
  identifiers of the spec criteria the chosen slice makes green, and copies no criterion
  prose into the parent body. *Verified by: automated assertion.*
- **AC3.** A spec acceptance criterion contains no implementation identifier — no function
  name, file path, endpoint, or symbol. *Verified by: automated assertion.*
- **AC4.** A spec acceptance criterion that carries no automated assertion names exactly one
  of the three sanctioned verification methods. *Verified by: automated assertion.*
- **AC5.** Given a spec criterion delivered by only one of two slices that serve it, the
  `## Slices` ledger records that criterion as partially covered. *Verified by: automated
  assertion.*
- **AC6.** Given a partially covered spec criterion, the next `/craft:slice` pass keeps the
  uncovered remainder in its candidate set. *Verified by: automated assertion.*
- **AC7.** Given every spec criterion recorded as fully covered, a `/craft:slice` pass
  reports the spec complete and chooses no further slice. *Verified by: automated
  assertion.*
- **AC8.** `/craft:slice`'s ledger reconcile is the sole writer of a ledger line's coverage
  field, a line is never edited after it is appended, and a criterion's coverage is the
  union across lines. *Verified by: automated assertion.*
- **AC9.** The slice close gate refuses `done` while any spec criterion the slice claims to
  make green lacks a recorded green observation. A criterion whose method is a manual check
  is discharged by the operator naming it at close. *Verified by: automated assertion.*

## Non-Goals

n/a — fixture only, trailing section present so the AC section is not the last in the
document.
