# Fixture: full literal coverage plus one non-canonical bullet-marked ledger line

Synthetic fixture spec body for `candidate_set.py` tests — not a real vault record. The
second ledger line below predates the canonical top-level `- ` bullet convention — marked
with a leading asterisk instead — so the bullet regex misses it entirely. The eligibility
rule must still catch it rather than silently reporting the union complete.

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

- **First slice** — a fixture value claim covering every declared criterion. (`task/first`, closed 2026-01-01, covers AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9)
* **Second slice** — a legacy entry marked with the wrong bullet character. (`task/second`, closed 2026-01-02)
