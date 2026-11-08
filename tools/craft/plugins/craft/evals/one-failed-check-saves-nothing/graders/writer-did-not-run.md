---
type: regex
pattern: 'pending_check\.py (?!describe)'
match: not_contains
target:
  source: file
  path: lore-calls.log
---
