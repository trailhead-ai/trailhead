---
type: regex
pattern: 'record create [^\n]*--kind task'
target:
  source: file
  path: lore-calls.log
---
