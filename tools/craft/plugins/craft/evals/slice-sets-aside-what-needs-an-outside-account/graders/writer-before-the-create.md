---
type: regex
pattern: 'set_aside\.py.*record create [^\n]*--kind task'
flags: s
target:
  source: file
  path: lore-calls.log
---
