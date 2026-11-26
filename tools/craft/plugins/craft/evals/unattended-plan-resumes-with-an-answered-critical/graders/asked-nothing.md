---
type: llm
focus: trace
---
Judge only the run's last assistant message. Earlier messages in the trace, such as progress notes while reviewers run, do not count.

Pass if that message poses no question to the user and waits on no answer in this session: it does not ask which option to take now, does not ask for confirmation or approval, and does not invite the user to reply before the work proceeds. A report of what was done or left open passes. That includes a question the run parked on the record for a later run: naming the parked question, its identifier, its options or a recommendation, and saying where an answer goes is part of the report, not a question to the user.

Fail if that message ends by asking the user anything, or asks the user to choose an option now so that this session can go on.
