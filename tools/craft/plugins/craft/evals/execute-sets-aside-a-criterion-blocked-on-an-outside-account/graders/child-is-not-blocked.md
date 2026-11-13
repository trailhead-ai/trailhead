---
type: regex
pattern: '"status": "blocked"'
match: not_contains
target:
  source: file
  path: vault/task/invoice-pdf-with-a-sales-tax-line.json
---
