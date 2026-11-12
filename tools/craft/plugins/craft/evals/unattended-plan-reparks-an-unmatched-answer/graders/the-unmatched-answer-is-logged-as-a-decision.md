---
type: regex
pattern: '## Unattended run[\s\S]*\n## Unattended run\n(?:(?!## Unattended run)[\s\S])*\*\*Decisions:\*\*(?:(?!\*\*Parked questions:\*\*)[\s\S])*Q1(?:(?!\*\*Parked questions:\*\*)[\s\S])*\*\*Parked questions:\*\*'
target:
  source: file
  path: vault/task/print-the-totals-of-a-csv-input.md
---
