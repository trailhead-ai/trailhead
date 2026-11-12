---
type: regex
pattern: '## Unattended run'
match: not_contains
target:
  source: file
  path: vault/task/count-words.md
---
