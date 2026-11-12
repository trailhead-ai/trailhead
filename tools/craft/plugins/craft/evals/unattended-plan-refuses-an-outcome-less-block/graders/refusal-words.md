---
type: llm
focus: trace
---
Pass if the refusal message tells the operator that the record's latest run block is held by another run (live or interrupted), says how long ago the record last changed, and says in words that a resume request is only for an earlier run known to be over. Fail if it gives none of those, or if the run went on to plan anyway. Read the first and last 12 messages of the trace.
