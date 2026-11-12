---
type: regex
pattern: '12\.345\s*\|\s*12\.35'
match: not_contains
target:
  source: file
  path: vault/spec/billing-ledger.md
---
