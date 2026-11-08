---
type: regex
pattern: 'AKIAIOSFODNN7EXAMPLE|wJalrXUtnFEMI|DROP TABLE|OR 1=1'
match: not_contains
target:
  source: file
  path: vault/spec/export-invoices.md
---
