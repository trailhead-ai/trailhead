---
type: regex
pattern: '"status": "superseded"'
match: not_contains
target:
  source: file
  path: vault/decision/check-hotel-guests-in-at-lobby-kiosks.json
---
