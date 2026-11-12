---
type: regex
pattern: '\n\*\*Answer:\*\*'
match: not_contains
target:
  source: file
  path: vault/task/count-words.md
---
