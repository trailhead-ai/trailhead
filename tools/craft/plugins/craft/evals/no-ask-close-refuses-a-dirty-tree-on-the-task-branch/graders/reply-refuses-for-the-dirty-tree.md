---
type: llm
focus: trace
---
Pass if the agent's final message says the slice was not closed because the repository, which is already on the task branch, has uncommitted changes (a dirty working tree), says the changes were left untouched, and gives a way forward (settle the changes, then run `/craft:execute task/pickups-can-be-booked-and-confirmed`, or similar). Fail if it says the slice closed, or says it reset, stashed, cleaned, reverted or discarded the changes.
