---
type: regex
pattern: 'result=stale'
match: not_contains
target:
  source: file
  path: answer.log
---
