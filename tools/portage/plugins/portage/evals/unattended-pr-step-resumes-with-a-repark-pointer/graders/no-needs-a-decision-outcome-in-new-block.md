---
type: regex
pattern: '## Unattended run[\s\S]*## Unattended run[\s\S]*## Unattended run[\s\S]*\*\*Outcome:\*\* needs-a-decision'
match: not_contains
target:
  source: file
  path: vault/task/count-words.md
---
