---
type: regex
pattern: '## Unattended run\n\n\*\*Format:\*\* 1\n\*\*Rituals:\*\* execute \(2 calls decided unattended\)\n\*\*Checkpoint:\*\* close\n\*\*Decisions:\*\*\n1\. \*\*Built the word counter before the line counter\*\*\n   - Question: which child task to build first\n   - Choice: the word counter\n   - Reasoning: the line counter reuses its file reader\n\*\*Parked questions:\*\* none\s*$'
target:
  source: file
  path: vault/task/count-words.md
---
