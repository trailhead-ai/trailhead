---
type: regex
pattern: '- Q2: \*\*[^\n]+\n(?:  - [^\n]*\n)*\*\*Answer:\*\*'
match: not_contains
target:
  source: file
  path: vault/task/count-words.md
---
