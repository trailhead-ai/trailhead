# Fixture: a concurrent double-append tears a partial-coverage entry's parenthetical off

Synthetic fixture spec body for `candidate_set.py` tests — not a real vault record. The
operator note below lands mid-write, as a concurrent second append would, and has no bullet
marker of its own — it reads as an ordinary continuation line of the entry above it, which
pushes that entry's trailing parenthetical off the end of the joined entry text. The dual
-field shape (a partial-coverage entry, here) fails closed the same as a full-coverage one:
the union under-reports rather than fabricates.

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

- **First slice** — a fixture value claim. (`task/first`, closed 2026-01-01, partially covers AC2)
Operator note: a concurrent second append landed mid-write and lost its own bullet marker.
- **Second slice** — a fixture value claim. (`task/second`, closed 2026-01-02, covers AC3)
