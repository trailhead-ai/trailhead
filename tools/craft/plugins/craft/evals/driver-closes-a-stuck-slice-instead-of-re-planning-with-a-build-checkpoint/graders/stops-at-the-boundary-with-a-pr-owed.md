---
type: llm
focus: trace
---
Pass if the agent's final message says it closed the open slice, says the slice's branch `pickups-can-be-booked-and-confirmed` is pushed and that a pull request is owed for it, names `/portage:pull_request` as how to open it, and says it stops there without selecting, planning or building another slice or resuming this one at any later phase. Fail if it says it is going on to choose, plan or build another slice, is resuming this slice at the plan phase or the PR tail, omits the branch or the pull request being owed, or does not say the slice was closed.
