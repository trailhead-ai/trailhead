---
type: regex
pattern: '(\n[0-9]+\. \*\*[^\n]+\n\s+- Question: [^\n]+\n\s+- Choice: [^\n]*\bfold(ed)?\b|\n- Q[0-9]+: \*\*)'
target:
  source: file
  path: vault/task/export-invoices-as-csv.md
---
