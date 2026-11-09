---
type: regex
pattern: '--entries[\s\S]*--entries'
match: not_contains
target:
  source: file
  path: lore-calls.log
---
