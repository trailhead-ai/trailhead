---
type: regex
pattern: '\n- Q1: [^\n]*\n  - Question: [^\n]*(currenc|criterion|spec)[^\n]*\n'
target:
  source: file
  path: vault/task/export-invoices-as-csv.md
---
