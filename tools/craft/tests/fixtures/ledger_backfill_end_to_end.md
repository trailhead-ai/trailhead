# Fixture: two legacy no-token lines reproducing this spec's shape

Synthetic fixture spec body — not a real vault record. Reproduces the shape of
`spec/acceptance-criteria-are-atomic-assertions-a-slice-carries`'s first two
`## Slices` lines before the legacy backfill: two `done` slices predate the
`**Covers:**` field, so neither ledger line carries a coverage token yet, and
`candidate_set.py` can never report `complete-eligible: yes` until they are
backfilled.

## Acceptance Criteria

- **AC1.** A fixture criterion, mirroring the first stranded identifier.
- **AC2.** A fixture criterion, mirroring the second stranded identifier.

## Slices

- **First legacy slice** — a fixture value claim predating the coverage field. (`task/legacy-one`, closed 2026-01-01)
- **Second legacy slice** — a fixture value claim predating the coverage field. (`task/legacy-two`, closed 2026-01-02)
