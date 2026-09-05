# Fixture: an unmarked trailing line re-attributes coverage to a wider join

Synthetic fixture spec body for `ledger_gate.py` tests — not a real vault record.
An operator note was appended below an existing canonical entry without going
through the reconcile. It carries no marker of its own, so `parse_ledger_entries`
(the entries view this gate certifies against) folds it into nothing and reports
only the entry's own bullet; but `parse_ledger`'s wider join (used by
`candidate_set.py`'s termination decision) folds the note's own trailing
parenthetical in as the entry's last parenthetical, widening its coverage to
AC2 as well — a decided coverage this gate never saw.

## Acceptance Criteria

- **AC1.** A fixture criterion.
- **AC2.** A fixture criterion, never built.

## Slices

- **Slice one** — claim. (`task/alpha`, closed 2026-01-01, covers AC1)
Operator note added later. (`task/alpha`, closed 2026-01-01, covers AC1, AC2)
