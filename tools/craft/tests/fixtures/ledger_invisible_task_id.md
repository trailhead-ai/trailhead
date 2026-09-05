# Fixture: a ledger entry whose task id carries no visible content

Synthetic fixture spec body for `ledger_gate.py` tests — not a real vault record. The task id between `task/` and the closing backtick below is a single U+200B ZERO WIDTH SPACE — it survives `str.strip()` but renders as nothing in any editor or terminal.

## Acceptance Criteria

- **AC1.** A fixture criterion.

## Slices

- **First slice** — a fixture value claim. (`task/​`, closed 2026-01-01, covers AC1)
