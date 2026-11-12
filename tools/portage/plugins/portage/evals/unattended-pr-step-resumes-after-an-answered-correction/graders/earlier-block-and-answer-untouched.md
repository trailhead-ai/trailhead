---
type: regex
pattern: '\*\*Outcome:\*\* needs-a-decision\n\*\*Checkpoint:\*\* needs-a-decision\n\*\*Decisions:\*\*[\s\S]*\*\*Parked questions:\*\*\n- Q1: [^\n]*\n  - Question: [^\n]*\n  - Options: [^\n]*\n  - Progress: [^\n]*\n\*\*Answer:\*\* \(a\)\n'
target:
  source: file
  path: vault/task/count-words.md
---
