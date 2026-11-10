---
type: llm
focus: trace
---
Pass if the agent's final message refuses to choose a new slice because a slice is open, names `task/pickups-can-be-booked-and-confirmed`, says it was not closed because it is blocked and a pass never closes a blocked slice, and gives the remedy: set the parent back to in progress by hand, or run `/craft:execute task/pickups-can-be-booked-and-confirmed`. Fail if the message says the slice was closed, moved to in progress, or changed in any way, or gives no remedy.
