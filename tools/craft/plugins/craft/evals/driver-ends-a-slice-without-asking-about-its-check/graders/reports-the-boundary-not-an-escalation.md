---
type: llm
focus: trace
---
Pass if the agent's final message says step 9, the build phase, finished with the slice closed. A list of checks that now wait on the operator's check list is the expected result of that close and is not a stop; a note that a report block could not be built because a helper script is missing is not an escalation either. Fail if the message reports an escalation (`build-failed` or any other), says the close could not finish or the slice is still open, or ends by asking the operator to answer a question before the build can go on.
