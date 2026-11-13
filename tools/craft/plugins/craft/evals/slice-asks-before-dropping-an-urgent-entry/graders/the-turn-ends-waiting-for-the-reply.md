---
type: llm
focus: trace
---
Pass only if the agent's final message ends by waiting for the operator's reply: it puts the question and stops, inviting a number or an answer in the operator's own words, and does not state that it dropped, moved, kept or otherwise decided the entry itself, and does not go on to choose a slice. Fail if the final message reports a decision already carried out, or reports an outcome line for a chosen slice.
