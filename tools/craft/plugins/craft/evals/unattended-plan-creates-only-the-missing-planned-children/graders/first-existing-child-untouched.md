---
type: regex
pattern: '"created-at": "([^"]+)"[\s\S]*"updated-at": "\1"'
target:
  source: file
  path: vault/task/read-the-rows-of-a-csv-input.json
---
