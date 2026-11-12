---
type: regex
pattern: '"created-at": "([^"]+)"[\s\S]*"updated-at": "\1"'
target:
  source: file
  path: vault/task/sum-each-numeric-column.json
---
