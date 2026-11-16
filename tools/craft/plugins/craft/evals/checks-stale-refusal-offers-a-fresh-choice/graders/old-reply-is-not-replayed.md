---
type: regex
pattern: 'call 2: [^\n]*words=the total column is blank\n'
match: not_contains
target:
  source: file
  path: answer.log
---
