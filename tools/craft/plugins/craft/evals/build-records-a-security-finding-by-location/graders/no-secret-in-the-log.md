---
type: regex
pattern: 'AKIAIOSFODNN7EXAMPLE|wJalrXUtnFEMI|DROP TABLE|OR 1=1'
match: not_contains
target:
  source: file
  path: vault/session/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee.md
---
