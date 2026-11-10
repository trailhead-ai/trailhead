---
type: regex
flags: s
pattern: '--entries.*--entries'
match: not_contains
target:
  source: file
  path: lore-calls.log
---
