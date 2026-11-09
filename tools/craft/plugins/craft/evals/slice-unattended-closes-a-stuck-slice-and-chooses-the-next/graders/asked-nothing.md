---
type: llm
focus: trace
---
Pass if the run's final message poses no question to the user and waits on no answer, and the trace shows no question about any manual check put to the operator (no "Did you carry out this check?", no choice between "Yes, I checked it" / "Not yet"). A report of what was decided passes. Fail if the final message ends by asking the user anything, or any question about a check appears.
