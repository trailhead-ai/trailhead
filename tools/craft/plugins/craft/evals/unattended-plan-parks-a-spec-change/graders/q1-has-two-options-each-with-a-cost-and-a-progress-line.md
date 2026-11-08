---
type: regex
pattern: '## Unattended run[\s\S]*\n- Q1: \*\*[^\n]+\n  - Question: [^\n]+\n  - Options: \(a\) [^\n]*costs[^\n]*; \(b\) [^\n]*costs[^\n]*\n  - Progress: [^\n]+'
target:
  source: file
  path: vault/task/write-totals-to-a-named-report-file.md
---
