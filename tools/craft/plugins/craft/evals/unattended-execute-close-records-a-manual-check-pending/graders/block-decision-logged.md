---
type: regex
pattern: '## Unattended run(?=[\s\S]*AC2)[\s\S]*\n[0-9]+\. \*\*[^\n]+\n\s+- Question: [^\n]+\n\s+- Choice: [^\n]+\n\s+- Reasoning: [^\n]+'
target:
  source: file
  path: vault/task/export-invoices-as-csv.md
---
