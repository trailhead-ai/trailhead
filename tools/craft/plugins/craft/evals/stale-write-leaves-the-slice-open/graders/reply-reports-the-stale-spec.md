---
type: llm
focus: trace
---
Pass if the agent's final message says the slice was not closed and stays in progress, says the spec changed while the close was writing to it (reason `stale-spec`, or close to it), that the check was not added and nothing was overwritten, and tells the operator to run the close again. Fail if the message says the slice closed, says the check was added to the list, or does not say why the close stopped.
