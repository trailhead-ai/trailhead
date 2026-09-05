# Fixture: duplicate task id whose credential-shaped secret is split by a raw control byte

Synthetic fixture spec body for `ledger_gate.py` tests -- not a real vault record. The
task id is an AWS-access-key-shaped string (`AKIA[0-9A-Z]{16}`) with a single raw control
byte spliced into the middle of the 16-character suffix. `_neutralize_control_chars`
escapes that byte to the literal text `\x01` BEFORE the credential scrub runs, which
splits the match exactly as the raw byte would -- the scrub's character classes exclude
`\`, `x`, and hex digits, so `AKIA1234\x01567890ABCDEF` does not match the AKIA pattern
either before or after escaping unless the scrub's decision is made on a collapsed view.

## Acceptance Criteria

- **AC1.** A fixture criterion.

## Slices

- **First slice** -- a fixture value claim. (`task/AKIA1234567890ABCDEF`, closed 2026-01-01, covers AC1)
- **Second slice** -- another value claim. (`task/AKIA1234567890ABCDEF`, closed 2026-01-02, covers AC1)
