---
type: regex
pattern: '\n- Q1: (?:[^\n]*AC\d+[^\n]*AC\d+[^\n]*\n  - Question: [^\n]*|[^\n]*\n  - Question: [^\n]*AC\d+[^\n]*AC\d+[^\n]*)\n  - Options: [^\n]*\n  - Progress: [^\n]+'
target:
  source: file
  path: vault/task/move-the-shop-data-into-one-database.md
---
