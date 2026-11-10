---
type: regex
pattern: '\*\*F3\.\*\*[^\n]*\n\s*- status: dropped: (?:nowhere:|task/[a-z0-9-]+)'
target:
  source: file
  path: vault/spec/tally-report.md
---
