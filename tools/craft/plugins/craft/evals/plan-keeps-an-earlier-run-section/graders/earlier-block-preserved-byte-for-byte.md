---
type: regex
pattern: '## Unattended run\n\n\*\*Format:\*\* 1\n\*\*Rituals:\*\* slice \(1 calls decided unattended\)\n\*\*Outcome:\*\* slice-chosen\n\*\*Checkpoint:\*\* slice materialized\n\*\*Decisions:\*\*\n1\. \*\*Chose the totals slice first\*\*\n   - Question: which slice comes next for the report export\n   - Choice: the slice that writes totals to a named report file\n   - Reasoning: it is the smallest slice that delivers a report an operator can open\n\*\*Parked questions:\*\* none'
target:
  source: file
  path: vault/task/write-totals-to-a-named-report-file.md
---
