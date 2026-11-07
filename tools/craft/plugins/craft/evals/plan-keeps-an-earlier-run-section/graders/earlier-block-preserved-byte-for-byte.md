---
type: regex
pattern: '## Unattended run\n\n\*\*Format:\*\* 1\n\*\*Rituals:\*\* slice \(1 calls decided unattended\), plan \(0 calls decided unattended\)\n\*\*Outcome:\*\* needs-a-decision\n\*\*Checkpoint:\*\* plan parked a question about report delivery\n\*\*Decisions:\*\*\n1\. \*\*Chose the totals slice first\*\*\n   - Question: which slice comes next for the report export\n   - Choice: the slice that writes totals to a named report file\n   - Reasoning: it is the smallest slice that delivers a report an operator can open\n\*\*Parked questions:\*\*\n- Q1: \*\*Whether the nightly job also emails the report\*\*\n  - Question: the spec says nothing about delivering the report beyond writing the file\n  - Options: \(a\) leave the report as a file only — costs nothing extra \(recommended\); \(b\) also email it — costs a mail dependency\n  - Progress: nothing built; the report-file slice does not depend on the answer'
target:
  source: file
  path: vault/task/write-totals-to-a-named-report-file.md
---
