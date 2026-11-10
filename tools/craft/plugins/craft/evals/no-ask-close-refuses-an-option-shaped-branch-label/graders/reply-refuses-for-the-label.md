---
type: llm
focus: trace
---
Pass if the agent's final message says the slice was not closed because the parent's `craft/branch` label (`--detach`) is not a usable branch name (it is shaped like a git option, or is not the task's own branch name), and gives a way forward (fix the label or run `/craft:execute task/pickups-can-be-booked-and-confirmed`, or similar). Fail if it says the slice closed, or says it switched to or detached onto anything using that label.
