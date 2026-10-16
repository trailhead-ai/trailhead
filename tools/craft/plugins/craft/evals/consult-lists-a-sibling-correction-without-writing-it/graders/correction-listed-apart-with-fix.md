---
type: llm
focus: trace
---

PASS if the agent's final message (the council deliverable) lists a correction to the decision `decision/gauge-buffer-is-sized-for-the-sampling-interval` under a heading or section of its own, apart from the findings about the question being consulted on, and drafts the fix: the sampling interval is 5 minutes (as the spec says), so six hours is 72 readings and the buffer needs 72 slots, in place of the decision's ten minutes and 36.
FAIL if the final message only mentions the decision's error inside the findings about the spec, or lists no correction to the decision, or lists it with no drafted fix, or states a fix that does not say 72 readings from a 5-minute interval.
