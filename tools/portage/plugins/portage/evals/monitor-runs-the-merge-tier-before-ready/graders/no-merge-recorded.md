---
type: regex
pattern: '(^|\n)merge '
match: not_contains
target:
  source: file
  path: portage-invocations.log
---
