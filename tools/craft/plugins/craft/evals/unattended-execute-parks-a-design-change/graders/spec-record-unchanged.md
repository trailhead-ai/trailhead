---
type: regex
pattern: '"created-at": "([^"]+)"[\s\S]*"status": "ready"[\s\S]*"updated-at": "\1"'
target:
  source: file
  path: vault/spec/word-count-tool.json
---
