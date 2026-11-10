---
type: regex
pattern: '"status": "done"'
match: not_contains
target:
  source: file
  path: vault/task/pickups-can-be-booked-and-confirmed.json
---
