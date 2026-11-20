---
type: regex
pattern: '\n- Q\d+:'
match: not_contains
target:
  source: file
  path: vault/task/export-the-orders-to-a-csv-file.md
---
