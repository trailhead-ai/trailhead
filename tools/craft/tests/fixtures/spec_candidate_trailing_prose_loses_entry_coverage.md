# Fixture: trailing prose after an entry loses that entry's coverage

Synthetic fixture spec body for `candidate_set.py` tests — not a real vault record. The
operator note below sits between two canonical entries, with no marker of its own, so it
reads as an ordinary continuation line of the entry above it — which pushes that entry's
trailing parenthetical off the end of the joined entry text and makes the whole entry
unparseable. The entry's own declared coverage is lost, and the union is fail-closed
rather than fabricated complete.

## Acceptance Criteria

- **AC1.** A fixture criterion.
- **AC2.** A fixture criterion.
- **AC3.** A fixture criterion.
- **AC4.** A fixture criterion.
- **AC5.** A fixture criterion.
- **AC6.** A fixture criterion.
- **AC7.** A fixture criterion.
- **AC8.** A fixture criterion.
- **AC9.** A fixture criterion.

## Slices

- **First slice** — a value claim. (`task/first`, closed 2026-01-01, covers AC1, AC2)
Operator note: the above slice landed a week late.
- **Second slice** — a value claim. (`task/second`, closed 2026-01-02, covers AC3, AC4)
