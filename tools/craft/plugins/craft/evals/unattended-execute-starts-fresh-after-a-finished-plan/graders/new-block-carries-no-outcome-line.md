---
type: regex
pattern: '## Unattended run[\s\S]*\n## Unattended run\n(?:(?!## Unattended run)(?!\*\*Outcome:)[\s\S])*$'
target:
  source: file
  path: vault/task/count-words.md
---
