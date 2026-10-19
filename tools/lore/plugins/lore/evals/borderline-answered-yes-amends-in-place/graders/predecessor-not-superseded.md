---
type: regex
pattern: '"status": "superseded"'
match: not_contains
target:
  source: file
  path: vault/decision/rebalance-bike-docks-with-overnight-van-runs.json
---
