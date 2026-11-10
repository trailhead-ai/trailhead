---
type: regex
pattern: '\*\*F4\.\*\*[^\n]*\n\s*- status: (?!dropped)'
target:
  source: file
  path: vault/spec/tally-report.md
---
