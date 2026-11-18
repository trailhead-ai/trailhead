---
type: regex
pattern: '"criterion"\s*:\s*"AC[23]"'
match: not_contains
target:
  source: file
  path: set-aside-entries.log
---
