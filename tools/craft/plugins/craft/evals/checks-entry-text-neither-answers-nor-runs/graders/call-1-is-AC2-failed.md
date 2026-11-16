---
type: regex
pattern: 'call 1: vault=levr spec=spec/[a-z-]+ criterion=AC2 slice=[^\n ]+ answer=failed base=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa words=the total column is blank\n'
target:
  source: file
  path: answer.log
---
