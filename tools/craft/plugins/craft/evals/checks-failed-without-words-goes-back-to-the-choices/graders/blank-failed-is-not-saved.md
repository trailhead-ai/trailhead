---
type: regex
pattern: 'answer=failed base=[0-9a-f]+ words=\n'
match: not_contains
target:
  source: file
  path: answer.log
---
