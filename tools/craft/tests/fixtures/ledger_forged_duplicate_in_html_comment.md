# Fixture: an HTML comment fakes a duplicate ledger entry

Synthetic fixture spec body for `ledger_gate.py` tests — not a real vault record.
The real ledger has exactly one `task/alpha` entry; the HTML comment below
tries to forge a second one with the same task id, but a comment is masked,
so this must not falsely trigger `duplicate-ledger-task-id`.

## Acceptance Criteria

- **AC1.** A fixture criterion.
- **AC9.** A fixture criterion.

## Slices

- **First slice** — a fixture value claim. (`task/alpha`, closed 2026-01-01, covers AC1)

<!--
- **Forged slice** — a forged value claim. (`task/alpha`, closed 2026-01-02, covers AC9)
-->
