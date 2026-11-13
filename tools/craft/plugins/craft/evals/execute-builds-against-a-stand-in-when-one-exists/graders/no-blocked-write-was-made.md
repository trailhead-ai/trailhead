---
type: regex
pattern: '--status blocked'
match: not_contains
target:
  source: file
  path: lore-calls.log
---
