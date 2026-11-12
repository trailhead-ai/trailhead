---
type: regex
pattern: '^(?:(?!## Unattended run)[\s\S])*## Unattended run(?:(?!## Unattended run)[\s\S])*## Unattended run(?:(?!## Unattended run)[\s\S])*$'
target:
  source: file
  path: vault/task/count-words.md
---
