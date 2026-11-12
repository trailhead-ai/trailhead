---
type: regex
pattern: '\n- Q[0-9]+:'
match: not_contains
target:
  source: file
  path: vault/task/count-words.md
---
