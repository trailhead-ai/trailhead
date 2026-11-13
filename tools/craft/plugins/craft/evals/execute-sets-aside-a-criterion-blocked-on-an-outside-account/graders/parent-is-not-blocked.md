---
type: regex
pattern: '"status": "blocked"'
match: not_contains
target:
  source: file
  path: vault/task/members-can-download-invoices-with-sales-tax.json
---
