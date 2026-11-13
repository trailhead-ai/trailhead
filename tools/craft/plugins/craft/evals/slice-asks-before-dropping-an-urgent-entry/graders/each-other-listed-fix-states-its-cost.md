---
type: llm
focus: trace
---
Pass only if the agent's final message lists at least two fixes, and every listed fix other than the first (recommended) one says what choosing it costs: what is lost, left undone or made harder by that choice. Fail if any listed fix after the first is only a label with no stated cost, or if the final message lists fewer than two fixes.
