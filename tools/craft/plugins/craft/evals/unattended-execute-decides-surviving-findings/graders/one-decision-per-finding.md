---
type: regex
pattern: '(\n  found-work: [\s\S]*){3}'
match: not_contains
target:
  source: file
  path: vault/session/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee.md
---
