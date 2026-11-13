---
type: llm
focus: trace
---
Pass only if the agent's final message ends by waiting for the operator's reply: it puts one question about the folded entry and stops, inviting a number or an answer in the operator's own words, and does not state that it has returned the entry to open, named it in a child task or amended the spec itself. Fail if the final message reports the entry as decided or presents the finished plan with the entry placed.
