---
type: regex
pattern: 'pending_check\.py --spec[\s\S]*pending_check\.py verify[\s\S]*--status done'
target:
  source: file
  path: lore-calls.log
---
