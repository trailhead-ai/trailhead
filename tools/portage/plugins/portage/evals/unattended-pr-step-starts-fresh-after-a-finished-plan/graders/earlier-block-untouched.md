---
type: regex
pattern: '## Unattended run\n\n\*\*Format:\*\* 1\n\*\*Rituals:\*\* plan \(1 calls decided unattended\)\n\*\*Outcome:\*\* plan-ready\n\*\*Checkpoint:\*\* plan written\n\*\*Decisions:\*\*\n1\. \*\*Kept the plan to two tasks\*\*\n   - Question: how many child tasks the plan holds\n   - Choice: two\n   - Reasoning: the slice is small\n\*\*Parked questions:\*\* none'
target:
  source: file
  path: vault/task/count-words.md
---
