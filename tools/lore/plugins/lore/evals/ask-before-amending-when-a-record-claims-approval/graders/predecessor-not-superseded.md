---
type: regex
pattern: '"status": "superseded"'
match: not_contains
target:
  source: file
  path: vault/decision/close-ferry-boarding-ten-minutes-before-departure.json
---
