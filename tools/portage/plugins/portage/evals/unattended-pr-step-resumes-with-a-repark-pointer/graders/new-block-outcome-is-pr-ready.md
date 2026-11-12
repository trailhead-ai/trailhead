---
type: regex
pattern: '## Unattended run[\s\S]*## Unattended run[\s\S]*## Unattended run[\s\S]*\*\*Outcome:\*\* pr-ready\n'
target:
  source: file
  path: vault/task/count-words.md
---
