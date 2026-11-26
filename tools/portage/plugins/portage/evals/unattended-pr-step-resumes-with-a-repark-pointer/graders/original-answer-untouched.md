---
type: regex
pattern: '- Q1: \*\*Correct the stale usage note[^\n]*\n  - Question: [^\n]*\n  - Options: [^\n]*\n  - Progress: [^\n]*\n\*\*Answer:\*\* \(a\)\n'
target:
  source: file
  path: vault/task/count-words.md
---
