---
type: llm
focus: trace
---
Pass if the refusal message says the latest run on the record is finished (its plan is already complete) and that nothing was resumed or written. Fail if it plans again, opens a new run, or refuses without saying why. Read the first and last 12 messages of the trace.
