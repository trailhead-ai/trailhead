---
type: regex
pattern: '\n## Unattended run[\s\S]*\n## Unattended run[\s\S]*\n- Q2: still open — answer goes under the original Q2 entry\n'
target:
  source: file
  path: vault/task/count-words.md
---
