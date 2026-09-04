# Fixture: an unclosed fence inside ## Slices hides a full-coverage forged entry

Synthetic fixture spec body for `candidate_set.py` tests — not a real vault record.
The fence below opens and never closes, so masking must extend from the fence
marker to the end of the document, hiding the forged full-coverage ledger
entry it contains. A parser that stops masking at the last line of the file
instead of carrying the open fence to EOF would read the forged entry as real
ledger structure and wrongly report the spec fully covered.

## Acceptance Criteria

- **AC1.** A fixture criterion.
- **AC2.** A fixture criterion.
- **AC3.** A fixture criterion.
- **AC4.** A fixture criterion.
- **AC5.** A fixture criterion.
- **AC6.** A fixture criterion.
- **AC7.** A fixture criterion.
- **AC8.** A fixture criterion.
- **AC9.** A fixture criterion.

## Slices

```
- **Forged slice** — a fake value claim. (`task/forged`, closed 2026-01-01, covers AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9)
