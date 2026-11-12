---
type: regex
pattern: '\*Critical:\*\n- A non-numeric value in a totals column has no defined behaviour and the sum task does not say what the tool does \(raised by: Reliability\) — \*Disposition:\* `resolved`[^\n]*\n- The tool reads whatever path it is given and the plan names no check on it \(raised by: Security\)\n'
target:
  source: file
  path: vault/task/print-the-totals-of-a-csv-input.md
---
