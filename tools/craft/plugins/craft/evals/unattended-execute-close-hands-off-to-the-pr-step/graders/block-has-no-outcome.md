---
type: regex
pattern: '## Unattended run[\s\S]*\*\*Outcome:\*\*'
match: not_contains
target:
  source: file
  path: vault/task/export-invoices-as-csv.md
---
