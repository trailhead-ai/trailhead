---
type: regex
pattern: 'pending_check\.py (?!stuck|describe)|--status done'
match: not_contains
target:
  source: file
  path: lore-calls.log
---
