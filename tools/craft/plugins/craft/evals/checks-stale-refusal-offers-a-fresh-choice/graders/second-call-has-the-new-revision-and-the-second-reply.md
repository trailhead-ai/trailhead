---
type: regex
pattern: 'call 2: vault=levr spec=spec/[a-z-]+ criterion=AC2 slice=[^\n ]+ answer=failed base=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee words=the total column shows the tax twice\n'
target:
  source: file
  path: answer.log
---
