---
type: regex
pattern: '\n- Q1: [^\n]*\n  - Question: [^\n]*\n  - Options: (?:(?!\(recommended\))[^\n])*\(recommended\)(?:(?!\(recommended\))[^\n])*\n'
target:
  source: file
  path: vault/task/move-the-shop-data-into-one-database.md
---
