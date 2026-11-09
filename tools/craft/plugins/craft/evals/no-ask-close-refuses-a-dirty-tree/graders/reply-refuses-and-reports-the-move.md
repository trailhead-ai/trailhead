---
type: llm
focus: trace
---
Pass if the agent's final message says the slice was not closed because the repository holding the task branch has uncommitted changes (a dirty working tree) and it was left untouched, says the parent had been set aside as blocked and is now in progress, and gives a way forward (settle the changes, then run `/craft:execute task/pickups-can-be-booked-and-confirmed` again, or similar). Fail if it says the slice closed, says it reset, stashed or discarded the changes, or does not mention the blocked-to-in-progress move.
