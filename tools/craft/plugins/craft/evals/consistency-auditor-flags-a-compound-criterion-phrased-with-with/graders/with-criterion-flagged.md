---
type: llm
focus: trace
---

Judge the consistency audit the run reports in its final message. Criterion 1 of the spec reads: "Support engineers can search tickets by customer email, with results ranked by the relevance model."
PASS if the audit lists criterion 1 as a compound criterion (for example under a "Compound criteria" heading), splitting it into searching by customer email and ranking by the relevance model.
FAIL if the audit does not list criterion 1 as compound, or says it has no compound criteria.
