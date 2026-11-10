---
type: llm
focus: trace
---
Pass if the agent's final message refuses to choose a new slice because a slice is already open, names that open slice (the one about booking and confirming a pickup), says in one line that it was not closed for the operator because its build is not finished (a child task is not done), and offers resuming it with /craft:execute or dropping it. Fail if the message does not say why the slice was not closed, says the slice was closed, or goes on to choose a next slice.
