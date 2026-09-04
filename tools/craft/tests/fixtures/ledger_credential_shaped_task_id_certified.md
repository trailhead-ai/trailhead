# Fixture: a single credential-shaped task id on the certified (exit-0) path

Synthetic fixture spec body for `ledger_gate.py` tests — not a real vault record. Unlike
`ledger_credential_shaped_duplicate_task_id.md`, this task id names only one entry, so the
gate certifies (exit 0) — the CERTIFIED stdout block itself must scrub the credential-shaped
task id, not just the `reason:` line on a refusal.

## Acceptance Criteria

- **AC1.** A fixture criterion.

## Slices

- **First slice** — a fixture value claim. (`task/deploy-sk_live_Zq7Kd2`, closed 2026-01-01, covers AC1)
