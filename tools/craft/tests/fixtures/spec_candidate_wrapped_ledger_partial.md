# Fixture: wrapped multi-line ledger entries carrying partial-coverage fields

Synthetic fixture spec body for `candidate_set.py` tests — not a real vault record. Mirrors
the real vault ledger shape: each entry wraps its value-claim prose across several physical
lines, with the trailing `(`task/<id>`, closed <date>, ...)` parenthetical — carrying a
partial-coverage field — landing on its own continuation line.

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

- **First slice** — a wrapped value claim that runs long enough to wrap across more than
  one physical line before it reaches its own trailing parenthetical, which lands on a
  continuation line by itself.
  (`task/first`, closed 2026-01-01, partially covers AC2)
- **Second slice** — a second wrapped entry, also spanning several physical lines of prose
  before its own parenthetical closes it out on a continuation line of its own.
  (`task/second`, closed 2026-01-02, covers AC5, partially covers AC7)
