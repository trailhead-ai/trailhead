---
type: regex
pattern: '[Cc]overs:\*\*'
match: not_contains
target:
  source: file
  path: created-bodies.log
---
