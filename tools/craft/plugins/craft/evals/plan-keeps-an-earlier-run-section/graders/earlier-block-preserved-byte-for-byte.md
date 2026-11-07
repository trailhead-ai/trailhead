---
type: regex
pattern: '## Unattended run\n\n\*\*Format:\*\* 1\n\*\*Rituals:\*\* slice \(1 calls decided unattended\), plan \(0 calls decided unattended\)\n\*\*Outcome:\*\* needs-a-decision\n\*\*Checkpoint:\*\* plan parked a question before the approach choice\n\*\*Decisions:\*\*\n1\. \*\*Chose the totals slice first\*\*\n   - Question: which slice comes next for the report export\n   - Choice: the slice that writes totals to a named report file\n   - Reasoning: it is the smallest slice that delivers a report an operator can open\n\*\*Parked questions:\*\*\n- Q1: \*\*Which date format the report header uses\*\*\n  - Question: the header names the run date and the spec does not say how it is written\n  - Options: \(a\) ISO 8601 — costs nothing extra; \(b\) the operator locale — costs a locale lookup \(recommended\)\n  - Progress: nothing built; the approach choice and the tasks are not yet written'
target:
  source: file
  path: vault/task/write-totals-to-a-named-report-file.md
---
