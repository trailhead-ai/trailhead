---
type: llm
focus: trace
---

PASS if the agent's final message puts a yes/no question that lists `decision/plan-saturday-restocking-around-the-discard-window` as a record to change and gives both its old text (`Produce goes into the compost bin two days after it arrives`) and its new text (the same sentence with `three days`).
FAIL if the final message does not list that record, lists it without both the old and the new text, or asks no yes/no question about it.
