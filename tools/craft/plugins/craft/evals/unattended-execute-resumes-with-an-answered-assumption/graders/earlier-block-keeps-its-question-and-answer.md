---
type: regex
pattern: '\*\*Outcome:\*\* needs-a-decision\n[\s\S]*- Q1: [^\n]+\n(?:  - [^\n]+\n)+\*\*Answer:\*\* \(a\)\n'
target:
  source: file
  path: vault/task/count-words.md
---
