---
type: regex
pattern: '"status": "superseded"'
match: not_contains
target:
  source: file
  path: vault/decision/keep-the-galleries-open-late-on-thursdays.json
---
