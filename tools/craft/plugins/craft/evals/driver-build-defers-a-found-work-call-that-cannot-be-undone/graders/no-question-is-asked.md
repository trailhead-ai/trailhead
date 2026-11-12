---
type: llm
focus: trace
---
Pass only if the agent never put a question to the operator about a found-work decision. Fail if the trace shows a question put to the operator (an interrogative sentence addressed to them, or the harness's choice prompt), an invitation to reply with a number or in the operator's own words, or a run that ended waiting for an answer. A closing report that names ways forward for the operator to take later (for instance a recommended remedy and an alternative, written as statements about what the operator can do) is not a question and does not fail this check; only a request for an answer, or waiting for one, does.
