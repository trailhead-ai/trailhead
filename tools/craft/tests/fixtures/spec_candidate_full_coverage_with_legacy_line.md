# Fixture: fully covered by identifier, but one ledger line carries no coverage token

Synthetic fixture spec body for `candidate_set.py` tests — not a real vault record.
The `Third slice` line predates the coverage field and keeps the four-field
shape — no coverage token at all, never a fabricated full-coverage claim.

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

- **First slice** — a fixture value claim. (`task/first`, closed 2026-01-01, covers AC1, AC2, AC3, AC4, AC5)
- **Second slice** — a fixture value claim. (`task/second`, closed 2026-01-02, covers AC6, AC7, AC8, AC9)
- **Third slice** — a fixture value claim predating the coverage field. (`task/third`, closed 2026-01-03)
