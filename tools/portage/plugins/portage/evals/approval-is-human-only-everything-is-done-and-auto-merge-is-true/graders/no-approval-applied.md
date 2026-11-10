---
type: regex
pattern: '(--approve|add-label|APPROVE|/labels)'
match: not_contains
target:
  source: file
  path: gh-invocations.log
---
