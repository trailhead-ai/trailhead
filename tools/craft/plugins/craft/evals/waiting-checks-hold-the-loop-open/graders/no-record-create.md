---
type: regex
pattern: 'record create'
match: not_contains
target:
  source: file
  path: lore-calls.log
---
