# Fixture: legitimate wrapped multi-line ledger entries

Synthetic fixture spec body for `ledger_gate.py` tests — not a real vault record. Modeled
on the real vault ledger shape (see `spec/acceptance-criteria-are-atomic-assertions-a-
slice-carries`'s `## Slices` section): every entry below wraps its value-claim prose across
several physical lines, with continuation lines indented under their own bullet — the
shape every entry in that live spec actually uses, not the single-line shape every other
fixture in this file carries.

## Acceptance Criteria

- **AC1.** A fixture criterion.
- **AC2.** A fixture criterion.
- **AC3.** A fixture criterion.
- **AC4.** A fixture criterion.

## Slices

- **First slice** — a wrapped value claim that runs long enough to span across several
  physical lines of prose before it reaches its own trailing parenthetical, which
  lands on a continuation line by itself, mirroring the real vault ledger's stored
  shape exactly.
  (`task/first`, closed 2026-01-01, covers AC1, AC2)
- **Second slice** — a second wrapped entry whose continuation line carries a partial-
  coverage token instead of a full one, closing out on its own continuation line.
  (`task/second`, closed 2026-01-02, partially covers AC3)
- **Third slice** — a legacy-shaped wrapped entry whose parent has not yet recorded a
  coverage token for this line, appended before the monotonic backfill rule existed,
  spanning several physical lines just like the others above it.
  (`task/third`, closed 2026-01-03)
