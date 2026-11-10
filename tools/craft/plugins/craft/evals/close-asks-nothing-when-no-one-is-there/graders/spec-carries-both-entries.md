---
type: regex
flags: s
pattern: '## Waiting.*\*\*AC2\.\*\*.*\*\*AC3\.\*\*|## Waiting.*\*\*AC3\.\*\*.*\*\*AC2\.\*\*'
target:
  source: file
  path: vault/spec/schedule-courier-pickups.md
---
