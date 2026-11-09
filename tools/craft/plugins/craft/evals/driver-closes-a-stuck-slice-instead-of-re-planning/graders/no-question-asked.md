---
type: llm
focus: trace
---
Pass only if the agent never put a question about any manual check to the operator: it never asked whether a check was carried out, never offered the choices "Yes, I checked it" / "Not yet" / "I checked it and it failed", and never stopped to wait for an answer about a check. Fail if any such question or choice list appears anywhere in the trace, or if the run ended waiting for a reply about a check.
