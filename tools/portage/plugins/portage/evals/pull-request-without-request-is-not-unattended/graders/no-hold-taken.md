---
type: regex
pattern: 'portage hold '
match: not_contains
target:
  source: file
  path: portage.log
---
