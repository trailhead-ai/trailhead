# Fixture: a ledger entry's trailing parenthetical reached across a blank line

Synthetic fixture spec body for `candidate_set.py` tests — not a real vault record. The
first entry's trailing parenthetical lands on an indented continuation line separated from
the bullet by a blank line — a loose-list gap, per CommonMark, not a break between entries.
`parse_ledger_entries` must still fold the indented line in across the blank line, the same
way `parse_ledger`'s own join already does.

## Acceptance Criteria

- **AC1.** A fixture criterion.
- **AC2.** A fixture criterion.
- **AC3.** A fixture criterion.

## Slices

- **First slice** — a fixture value claim that pauses before its own parenthetical.

  (`task/first`, closed 2026-01-01, covers AC1, AC2)
- **Second slice** — a fixture value claim. (`task/second`, closed 2026-01-02, covers AC3)
