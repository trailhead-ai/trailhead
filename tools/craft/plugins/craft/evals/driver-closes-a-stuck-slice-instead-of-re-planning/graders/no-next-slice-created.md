---
type: regex
pattern: 'record create --kind task'
match: not_contains
target:
  source: file
  path: lore-calls.log
---
