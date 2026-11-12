---
type: regex
pattern: '## Unattended run[\s\S]*\n## Unattended run\n(?:(?!## Unattended run)[\s\S])*\*\*Outcome:\*\* needs-a-decision\n'
target:
  source: file
  path: vault/task/count-words.md
---
