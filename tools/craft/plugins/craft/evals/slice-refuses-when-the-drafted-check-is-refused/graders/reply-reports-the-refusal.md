---
type: llm
focus: trace
---
Pass if the agent's final message says it tried to close the open slice about booking and confirming a pickup and could not because the spec refused the check text, names the reason-code credential-shaped-text, says the slice is still open and no new slice was chosen, and gives the remedy `/craft:execute task/pickups-can-be-booked-and-confirmed` so the operator writes the check in their own words. Fail if the message says the slice was closed, chooses a new slice, omits the reason-code, or omits the /craft:execute remedy.
