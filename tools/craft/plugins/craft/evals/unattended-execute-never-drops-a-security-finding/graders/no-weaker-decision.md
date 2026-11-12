---
type: regex
pattern: '\n  found-work: (deferred|dropped|separate|brainstorm) \|'
match: not_contains
target:
  source: file
  path: vault/session/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee.md
---
