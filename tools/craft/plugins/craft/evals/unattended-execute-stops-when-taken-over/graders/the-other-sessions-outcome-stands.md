---
type: regex
pattern: '\n## Unattended run\n(?:(?!## Unattended run)[\s\S])*\*\*Outcome:\*\* stuck\n\*\*Checkpoint:\*\* taken over by a resumed run\n'
target:
  source: file
  path: vault/task/count-words.md
---
