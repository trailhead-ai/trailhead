---
type: regex
pattern: 'pending_check\.py'
match: not_contains
target:
  source: file
  path: lore-calls.log
---
