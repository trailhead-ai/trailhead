# Fixture: duplicate task id carrying a credential-shaped secret

Synthetic fixture spec body for `ledger_gate.py` tests — not a real vault record. The
task id itself embeds a credential-shaped string, so the gate's `reason:` line — which
echoes the offending task id verbatim — must be run through the credential-pattern scrub
before it reaches stdout/stderr.

## Acceptance Criteria

- **AC1.** A fixture criterion.

## Slices

- **First slice** — a fixture value claim. (`task/deploy-sk_live_Zq7Kd2`, closed 2026-01-01, covers AC1)
- **Second slice** — another value claim. (`task/deploy-sk_live_Zq7Kd2`, closed 2026-01-02, covers AC1)
