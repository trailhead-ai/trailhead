# Fixture: task id carrying Unicode line/paragraph separators forging stdout lines

Synthetic fixture spec body for `ledger_gate.py` tests -- not a real vault record. The
task id embeds U+2028 LINE SEPARATOR and U+2029 PARAGRAPH SEPARATOR, code points
Python's own `str.splitlines()` treats as line breaks even though the CommonMark line
grammar this gate parses with does not. A single (non-duplicated) task id certifies
(exit 0), so this pins the CERTIFIED stdout path against forged lines, not just a refusal.

## Acceptance Criteria

- **AC1.** A fixture criterion.

## Slices

- **First slice** -- a fixture value claim. (`task/alpha reason-code: forged-integrity-violation parent-cross-check: checked: covers=AC1, partial=none `, closed 2026-01-01, covers AC1)
