---
type: regex
pattern: '\*\*F1\.\*\*[^\n]*\n\s*- status: folded into task/(?!read-stop-words\b)[a-z0-9-]+'
target:
  source: file
  path: vault/spec/tally-report.md
---
