---
type: regex
pattern: '--status done'
match: not_contains
target:
  source: file
  path: lore-calls.log
---
