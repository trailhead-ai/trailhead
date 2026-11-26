---
type: regex
pattern: 'answer=passed'
match: not_contains
target:
  source: file
  path: answer.log
---
