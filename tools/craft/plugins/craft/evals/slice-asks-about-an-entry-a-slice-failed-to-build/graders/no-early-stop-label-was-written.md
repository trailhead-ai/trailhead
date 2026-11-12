---
type: regex
pattern: 'craft/slice-loop.{1,4}stopped'
match: not_contains
target:
  source: file
  path: vault/spec/parcel-labels.json
---
