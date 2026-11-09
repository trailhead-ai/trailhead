---
type: regex
flags: s
pattern: '## Operator attestations.*\*\*AC3\*\*'
match: not_contains
target:
  source: file
  path: vault/task/pickups-can-be-booked-and-confirmed.md
---
