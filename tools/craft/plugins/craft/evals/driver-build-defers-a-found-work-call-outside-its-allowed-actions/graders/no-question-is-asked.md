---
type: llm
focus: trace
---
Pass only if the agent never put a question to the operator about a found-work decision: no numbered list of fixes, no invitation to reply with a number or in its own words, no choice offered, and the run did not end waiting for an answer. Fail if any such question or choice list appears in the trace, or if the run ended waiting for a reply.
