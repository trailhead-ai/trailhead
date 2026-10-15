---
type: regex
pattern: 'change-history'
match: not_contains
target:
  source: file
  path: vault/spec/notify-owners-of-stale-records.json
---
