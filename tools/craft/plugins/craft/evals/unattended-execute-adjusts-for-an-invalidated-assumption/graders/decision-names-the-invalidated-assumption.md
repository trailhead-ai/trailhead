---
type: regex
pattern: '\*\*Decisions:\*\*\n1\. \*\*[^\n]+\n   - Question: [^\n]*(read_text|UTF-8|utf-8|loader|as text|decod)[^\n]*\n   - Choice: [^\n]+\n   - Reasoning: [^\n]+'
target:
  source: file
  path: vault/task/count-words.md
---
