---
type: regex
pattern: '- Q2: \*\*[\s\S]*- Q2: \*\*'
match: not_contains
target:
  source: file
  path: vault/task/count-words.md
---
