---
type: regex
pattern: '## Unattended run[\s\S]*\n[0-9]+\. \*\*[^\n]+\n\s+- Question: [^\n]+\n\s+- Choice: [^\n]*\bfold(ed)?\b'
target:
  source: file
  path: vault/task/export-invoices-as-csv.md
---
