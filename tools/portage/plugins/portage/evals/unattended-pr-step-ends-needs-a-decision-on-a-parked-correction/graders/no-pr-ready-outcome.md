---
type: regex
pattern: '\*\*Outcome:\*\* pr-ready'
match: not_contains
target:
  source: file
  path: vault/task/count-words.md
---
