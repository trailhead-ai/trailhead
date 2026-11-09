---
type: llm
focus: trace
---
Pass if the agent's final message says step 9, the build phase, finished with the slice closed, and does not report an escalation or a stop for the operator's input. Fail if it reports a `build-failed` (or any other) escalation, says the build could not complete, says it is waiting on the operator, or does not say the slice closed.
