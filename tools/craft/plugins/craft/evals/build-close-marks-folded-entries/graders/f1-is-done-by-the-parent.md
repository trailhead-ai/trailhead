---
type: regex
pattern: '\*\*F1\.\*\*[^\n]*\n\s*- status: done by task/count-lines\n'
target:
  source: file
  path: vault/spec/tally-report.md
---
