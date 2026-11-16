---
type: regex
pattern: 'call 3: vault=levr spec=spec/[a-z-]+ criterion=AC2 slice=[^\n ]+ answer=failed base=ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff words=w three\n'
target:
  source: file
  path: answer.log
---
