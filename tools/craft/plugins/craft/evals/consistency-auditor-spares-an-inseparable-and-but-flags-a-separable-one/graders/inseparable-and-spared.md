---
type: llm
focus: trace
---

Judge the consistency audit the run reports in its final message. Criterion 1 of the spec reads: "Claiming a ticket assigns it to the claiming engineer and removes it from the unassigned queue."
PASS if the audit does not list criterion 1 as a compound criterion. Mentioning criterion 1 elsewhere, or explaining why it is not compound, is fine.
FAIL if the audit lists criterion 1 under its compound criteria, or recommends splitting criterion 1 into separately deliverable parts.
