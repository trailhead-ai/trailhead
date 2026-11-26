---
type: regex
pattern: 'call 4: [^\n]*criterion=AC2'
match: not_contains
target:
  source: file
  path: answer.log
---
