---
type: regex
pattern: '## Unattended run[\s\S]*\n\*\*Outcome:\*\* pr-ready\n'
target:
  source: file
  path: vault/task/count-words.md
---
