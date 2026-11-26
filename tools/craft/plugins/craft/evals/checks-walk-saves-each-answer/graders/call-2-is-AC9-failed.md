---
type: regex
pattern: 'call 2: vault=levr spec=spec/[a-z-]+ criterion=AC9 slice=[^\n ]+ answer=failed base=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb words=the tax line is missing for a UK address\n'
target:
  source: file
  path: answer.log
---
