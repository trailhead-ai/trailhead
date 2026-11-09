---
type: regex
pattern: 'craft/slice-loop=stopped'
match: not_contains
target:
  source: file
  path: lore-calls.log
---
