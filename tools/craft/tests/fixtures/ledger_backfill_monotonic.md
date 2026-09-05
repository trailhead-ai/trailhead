# Fixture: a no-token legacy line whose parent already carries coverage

Synthetic fixture spec body for `ledger_gate.py` tests — not a real vault record.
This is the legacy-backfill precondition: the line predates the coverage
field, so it carries neither `covers` nor `partially covers` yet, but its
parent record already declares coverage — a completion, not a mutation.

## Acceptance Criteria

- **AC1.** A fixture criterion.
- **AC2.** A fixture criterion.

## Slices

- **First slice** — a fixture value claim predating the coverage field. (`task/legacy`, closed 2026-01-01)
