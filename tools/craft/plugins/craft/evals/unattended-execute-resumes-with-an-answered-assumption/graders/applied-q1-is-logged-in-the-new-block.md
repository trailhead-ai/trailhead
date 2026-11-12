---
type: regex
pattern: '## Unattended run[\s\S]*\n## Unattended run[\s\S]*\n {2,}- Applied: Q1\n'
target:
  source: file
  path: vault/task/count-words.md
---
