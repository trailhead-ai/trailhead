---
type: regex
flags: s
pattern: 'pending_check\.py --spec.*pending_check\.py verify.*--status done'
target:
  source: file
  path: lore-calls.log
---
