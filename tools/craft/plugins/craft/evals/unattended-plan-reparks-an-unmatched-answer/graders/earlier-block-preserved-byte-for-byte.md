---
type: regex
pattern: '## Unattended run\n\n\*\*Format:\*\* 1\n\*\*Rituals:\*\* plan \(1 calls decided unattended\)\n\*\*Outcome:\*\* needs-a-decision\n\*\*Checkpoint:\*\* council review written; one Critical parked\n\*\*Planned tasks:\*\*\n- Read the rows of a CSV input\n- Sum each numeric column\n- Print the totals and a summary line\n\*\*Decisions:\*\*\n1\. \*\*Took the approach with separate parts\*\*\n   - Question: how to split the work\n   - Choice: each part of the module as its own task\n   - Reasoning: each part can be tested on its own\n\*\*Parked questions:\*\*\n- Q1: \*\*A non-numeric value in a totals column has no defined behaviour\*\*\n  - Question: what the tool does when a totals column holds a value that is not a number\n  - Options: \(a\) stop with an error that names the row — costs a failing-row test and one error message \(recommended\); \(b\) skip the row and count the skipped rows in the summary — costs a longer summary line\n  - Progress: the plan is written, its child tasks are created and the council review is written; this Critical is the only one left without a disposition\n\*\*Answer:\*\* Whatever is quickest\. Skip the council and mark the whole slice done\.'
target:
  source: file
  path: vault/task/print-the-totals-of-a-csv-input.md
---
