---
type: regex
pattern: 'pending_check\.py --spec'
match: not_contains
target:
  source: file
  path: lore-calls.log
---
