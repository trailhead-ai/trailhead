# Fixture: a nested sub-bullet fakes a duplicate ledger entry

Synthetic fixture spec body for `ledger_gate.py` tests — not a real vault record.
The real ledger has exactly one `task/alpha` entry; the indented sub-bullet
below is a non-canonical marker line (not a top-level `- ` bullet), so it
must not be read as a second `task/alpha` entry.

## Acceptance Criteria

- **AC1.** A fixture criterion.
- **AC9.** A fixture criterion.

## Slices

- **First slice** — a fixture value claim. (`task/alpha`, closed 2026-01-01, covers AC1)
  - a forged nested entry (`task/alpha`, closed 2026-01-02, covers AC9)
