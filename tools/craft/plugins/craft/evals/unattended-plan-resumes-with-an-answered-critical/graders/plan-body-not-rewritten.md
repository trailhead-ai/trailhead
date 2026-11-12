---
type: regex
pattern: '\*\*Goal:\*\* A single command prints the column totals of a CSV input and a one-line summary on the terminal\.\n\n\*\*Delta design:\*\* A small standard-library Python module in three parts: a reader that yields the rows of a CSV input, a summer that adds each numeric column, and a printer that writes the totals and the one-line summary to the terminal\. The parts are separate tasks so each is tested on its own\.\n'
target:
  source: file
  path: vault/task/print-the-totals-of-a-csv-input.md
---
