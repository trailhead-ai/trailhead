---
type: llm
focus: trace
---
Judge only the agent's last assistant message, and judge what this run did, not what it tells the operator a later run will do.

Pass if that message says it closed the open slice, says the slice's branch `pickups-can-be-booked-and-confirmed` is pushed and that a pull request is owed for it, names `/portage:pull_request` as how to open it, and says this run stops there. Saying what a later `/craft:drive` run will do, such as record this close or choose the next slice, is a pointer for the operator, not this run going on.

Fail if the message says this run is going on to choose, plan or build another slice, or is resuming this slice at the plan phase or the PR tail; or if it omits the branch or the pull request being owed, or does not say the slice was closed.
