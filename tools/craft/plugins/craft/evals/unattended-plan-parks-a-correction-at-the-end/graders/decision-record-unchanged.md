---
type: regex
pattern: '"created-at": "([^"]+)"[\s\S]*"updated-at": "\1"'
target:
  source: file
  path: vault/decision/chunk-count-follows-the-nightly-row-count.json
---
