---
type: regex
pattern: '## Unattended run[\s\S]*\n## Unattended run\n(?:(?!## Unattended run)[\s\S])*\*\*Outcome:\*\* plan-ready\n'
target:
  source: file
  path: vault/task/print-the-totals-of-a-csv-input.md
---
