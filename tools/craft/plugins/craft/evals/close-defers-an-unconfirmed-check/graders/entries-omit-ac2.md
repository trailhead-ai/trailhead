---
type: regex
pattern: 'criterion"\s*:\s*"AC2"'
match: not_contains
target:
  source: file
  path: pending-check-entries.log
---
