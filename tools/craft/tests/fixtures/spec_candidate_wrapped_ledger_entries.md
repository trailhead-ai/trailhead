# Fixture: wrapped multi-line ledger entries, mirroring the real vault ledger shape

Synthetic fixture spec body for `candidate_set.py` tests — not a real vault record. Each
ledger entry below wraps its value-claim prose across several physical lines, with the
trailing `(`task/<id>`, closed <date>, covers <...>)` parenthetical on its own continuation
line — the actual shape the live spec ledger stores.

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
  (`task/first`, closed 2026-01-01, covers AC2, AC7)
- **Second slice** — a second wrapped entry, also spanning several physical lines of prose
  before its own parenthetical closes it out on a continuation line of its own.
  (`task/second`, closed 2026-01-02, covers AC1, AC5)
