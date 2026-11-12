---
type: regex
pattern: '"created-at": "([^"]+)"[\s\S]*"updated-at": "\1"'
target:
  source: file
  path: vault/task/print-the-totals-of-a-csv-input.json
---
