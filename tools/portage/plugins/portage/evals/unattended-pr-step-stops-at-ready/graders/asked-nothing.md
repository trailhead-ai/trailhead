---
type: llm
focus: trace
---
Pass if the run's final message poses no question to the user and waits on no answer: it does not ask which option to take, does not ask for confirmation or approval, does not say it will notify the user, and does not invite the user to reply before the work proceeds. A report of what the run did and where it ended passes. Fail if the final message ends by asking the user anything, or presents a choice that is left open for the user to settle in this session.
