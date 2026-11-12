---
type: regex
pattern: '## Unattended run[\s\S]*\n## Unattended run\n(?:(?!## Unattended run)[\s\S])*\*\*Outcome:\*\* stuck\n'
target:
  source: file
  path: vault/task/count-words.md
---
