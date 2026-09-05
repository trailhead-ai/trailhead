# Fixture: duplicate task id carrying a raw ANSI control byte

Synthetic fixture spec body for `ledger_gate.py` tests. The task id itself carries
a raw ESC byte, which is legal input for a ledger entry's task-id text, so the gate's
`reason:` line must neutralize it before echoing the task id.

## Acceptance Criteria

- **AC1.** A fixture criterion.

## Slices

- **First slice** — a fixture value claim. (`task/deploy-[31mFAKE`, closed 2026-01-01, covers AC1)
- **Second slice** — another value claim. (`task/deploy-[31mFAKE`, closed 2026-01-02, covers AC1)
