---
type: regex
pattern: '\n- Q1: [^\n]*\n  - Question: [^\n]*(io_utils|read_text|loader|mechanism)[^\n]*\n'
target:
  source: file
  path: vault/task/count-words.md
---
