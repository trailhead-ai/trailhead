---
type: regex
pattern: '## Unattended run[\s\S]*\n## Unattended run\n(?:(?!## Unattended run)[\s\S])*\n- Q2: still open — answer goes under the original Q2 entry\n'
target:
  source: file
  path: vault/task/print-the-totals-of-a-csv-input.md
---
