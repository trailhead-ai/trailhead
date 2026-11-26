---
type: regex
pattern: 'exit=[1-9][0-9]* impl=untouched\n(?:.*\n)*exit=0 impl=changed'
target:
  source: file
  path: run-tests.log
---
