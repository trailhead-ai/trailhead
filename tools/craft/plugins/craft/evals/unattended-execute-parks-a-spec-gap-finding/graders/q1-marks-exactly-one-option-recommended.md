---
type: regex
pattern: '\n- Q1: [^\n]*\n  - Question: [^\n]*\n  - Options: (?:(?!\(recommended\))[^\n])*\(recommended\)(?:(?!\(recommended\))[^\n])*\n'
target:
  source: file
  path: vault/task/export-invoices-as-csv.md
---
