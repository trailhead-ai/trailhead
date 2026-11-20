---
type: regex
pattern: '\*\*Partially covers:\*\*[^\n]*AC[45]\b'
match: not_contains
target:
  source: file
  path: vault/task/export-the-orders-to-a-csv-file.md
---
